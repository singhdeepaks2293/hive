"""queen/coordinator.py -- the Queen: turns events into tasks, assigns owners,
enforces quorum and approval rules.

    inbox (syslog / traps / chat / approvals)
        -> normalize + dedup (storm collapse)
        -> Router.triage / route_request  -> TaskSpec batch
        -> publish to Blackboard (idempotent, dependency DAG)
        -> scheduler tick: promote deps, gate approvals, reap leases, backoff retries
        -> workers claim / heartbeat / complete / fail through the Queen

Every state transition is written to the append-only event log (AUDIT).
The Queen never touches devices; all device I/O stays behind the skill gateway.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Optional

from .blackboard import Blackboard
from .models import (ACTIVE_STATES, Approval, Event, EventKind, QueenConfig,
                     Risk, Task, TaskSpec, TaskState, now)
from .router import Router

log = logging.getLogger("hive.queen")


class QueenCoordinator:
    def __init__(self, board: Blackboard, router: Optional[Router] = None,
                 config: Optional[QueenConfig] = None) -> None:
        self.board = board
        self.router = router or Router()
        self.cfg = config or QueenConfig()
        self._inbox: asyncio.Queue[Event] = asyncio.Queue()
        self._recent: dict[str, tuple[Any, int]] = {}   # fingerprint -> (last_seen, count)
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False
        self._claim_lock = asyncio.Lock()   # serializes claims (needed once store does async I/O)
        self._wake = asyncio.Event()        # wakes the loop on ingest / stop
        self.metrics: dict[str, int] = {
            "events": 0, "deduped": 0, "storms": 0, "tasks_created": 0,
            "retries": 0, "approvals": 0, "rejected": 0,
        }

    # ------------------------------------------------------------ ingestion API
    async def ingest(self, event: Event) -> None:
        await self._inbox.put(event)
        self._wake.set()

    def ingest_nowait(self, event: Event) -> None:
        self._inbox.put_nowait(event)
        self._wake.set()

    async def request(self, user: str, text: str, device: Optional[str] = None,
                      **payload: Any) -> Event:
        ev = Event(kind=EventKind.REQUEST, source=user, text=text, device=device, payload=payload)
        await self.ingest(ev)
        return ev

    async def approve(self, task_id: str, approver: str,
                      decision: str = "approve", note: str = "") -> None:
        await self.ingest(Event(kind=EventKind.APPROVAL, source=approver,
                                payload={"task_id": task_id, "decision": decision, "note": note}))

    # ------------------------------------------------------------ worker API
    async def claim(self, worker: str) -> Optional[Task]:
        """Highest-priority READY task owned by `worker`, respecting backoff and
        the per-device concurrency cap. Increments attempts and starts the lease."""
        async with self._claim_lock:                     # one claimer at a time
            candidates = [t for t in await self.board.find([TaskState.READY])
                          if t.spec.owner == worker
                          and (t.not_before is None or t.not_before <= now())]
            candidates.sort(key=lambda t: (t.spec.priority, t.created_at))
            leased = await self.board.find([TaskState.LEASED])
            for task in candidates:
                if task.spec.device:
                    on_device = sum(1 for t in leased
                                    if t.spec.device == task.spec.device)
                    if on_device >= self.cfg.device_concurrency:
                        continue
                task.attempts += 1
                task.lease_holder = worker
                task.lease_expires = now() + timedelta(seconds=self.cfg.lease_ttl_s)
                await self._set_state(task, TaskState.LEASED, f"claimed by {worker}")
                return task
            return None

    async def heartbeat(self, task_id: str, worker: str) -> bool:
        """Extend the lease. False means the worker lost the lease and MUST stop."""
        task = await self.board.get(task_id)
        if (not task or task.state is not TaskState.LEASED
                or task.lease_holder != worker
                or task.lease_expires is None or task.lease_expires <= now()):
            return False                                 # expired lease cannot be resurrected
        task.lease_expires = now() + timedelta(seconds=self.cfg.lease_ttl_s)
        await self.board.save(task)
        return True

    async def complete(self, task_id: str, worker: str,
                       result: Optional[dict[str, Any]] = None) -> None:
        task = await self._check_holder(task_id, worker)
        task.result = result or {}
        await self._set_state(task, TaskState.SUCCEEDED, f"completed by {worker}")
        # Workers may propose follow-up tasks; the Queen stays the only publisher.
        followups = task.result.get("followups", [])
        if len(followups) > self.cfg.max_followups:
            await self._audit(task.id, "followups_capped",
                              f"{len(followups)} requested, {self.cfg.max_followups} allowed")
        for raw in followups[:self.cfg.max_followups]:
            try:
                await self._publish([TaskSpec(**raw)], origin=task.origin_event)
            except Exception as exc:                     # bad spec must not kill the Queen
                await self._audit(task.id, "followup_invalid", str(exc))

    async def fail(self, task_id: str, worker: str, error: str,
                   retryable: bool = True) -> None:
        task = await self._check_holder(task_id, worker)
        task.error = error
        if retryable:
            await self._retry_or_fail(task, f"worker error: {error}")
        else:
            await self._set_state(task, TaskState.FAILED, error)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._running = True
        self._loop_task = asyncio.create_task(self.run_forever(), name="queen")

    async def stop(self) -> None:
        """Graceful: finish the current tick, then exit. Never cancels mid-tick."""
        self._running = False
        self._wake.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None

    async def run_forever(self) -> None:
        log.info("Queen online. The hive awaits.")
        try:
            while self._running:
                self._wake.clear()
                await self.tick()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.tick_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("Queen offline.")

    async def tick(self) -> None:
        """One scheduling pass. Exposed so tests can drive time deterministically."""
        while not self._inbox.empty():
            ev = self._inbox.get_nowait()
            try:
                await self._handle_event(ev)
            except Exception as exc:                     # one bad event must not kill the hive
                log.exception("event %s failed", ev.id)
                await self._audit(None, "event_error", f"{ev.id}: {exc}")
        await self._advance()
        cutoff = now() - timedelta(seconds=self.cfg.dedup_window_s * 10)
        self._recent = {k: v for k, v in self._recent.items() if v[0] > cutoff}

    # ------------------------------------------------------------ event handling
    async def _handle_event(self, ev: Event) -> None:
        await self.board.append(ev)                      # raw event first: append-only truth
        self.metrics["events"] += 1
        if ev.kind in (EventKind.SYSLOG, EventKind.SNMP):
            await self._on_device_event(ev)
        elif ev.kind is EventKind.REQUEST:
            await self._publish(self.router.route_request(ev), origin=ev.id)
        elif ev.kind is EventKind.APPROVAL:
            await self._on_approval(ev)
        # AUDIT events are log-only.

    async def _on_device_event(self, ev: Event) -> None:
        fp = ev.fingerprint()
        last, count = self._recent.get(fp, (None, 0))
        if last is not None and now() - last < timedelta(seconds=self.cfg.dedup_window_s):
            count += 1
            self._recent[fp] = (now(), count)
            self.metrics["deduped"] += 1
            if count == self.cfg.storm_threshold:        # flap storm -> one critical task
                self.metrics["storms"] += 1
                spec = TaskSpec(type="diagnose", owner="analyst", device=ev.device, priority=0,
                                params={"focus": "event_storm", "mnemonic": ev.mnemonic or "",
                                        "count": count})
                await self._publish([spec], origin=ev.id)
                await self._audit(None, "storm_detected", f"{fp} x{count}")
            return
        self._recent[fp] = (now(), 1)
        await self._publish(self.router.triage(ev), origin=ev.id)

    async def _on_approval(self, ev: Event) -> None:
        task = await self.board.get(str(ev.payload.get("task_id", "")))
        if task is None or task.state is not TaskState.AWAITING_APPROVAL:
            await self._audit(ev.payload.get("task_id"), "approval_ignored",
                              "task not awaiting approval")
            return
        task.approvals.append(Approval(approver=ev.source,
                                       decision=ev.payload.get("decision", "approve"),
                                       note=ev.payload.get("note", "")))
        self.metrics["approvals"] += 1
        await self.board.save(task)
        await self._gate(task)                           # decide immediately, don't wait for tick

    # ------------------------------------------------------------ publishing
    async def _publish(self, specs: list[TaskSpec], origin: Optional[str]) -> list[Task]:
        active = await self.board.find(ACTIVE_STATES)
        by_key = {t.dedup_key(): t for t in active}
        batch: list[Task] = []
        for spec in specs:
            task = Task(spec=spec, origin_event=origin)
            key = task.dedup_key()
            if key in by_key:                            # idempotent: reuse active twin
                batch.append(by_key[key])
                await self._audit(by_key[key].id, "publish_deduped", f"origin={origin}")
                continue
            task.depends_on = [batch[i].id for i in spec.depends_on]
            await self.board.save(task)
            by_key[key] = task
            batch.append(task)
            self.metrics["tasks_created"] += 1
            await self._audit(task.id, "task_created",
                              f"type={spec.type} owner={spec.owner} origin={origin}")
        return batch

    # ------------------------------------------------------------ scheduler
    async def _advance(self) -> None:
        for task in await self.board.find(ACTIVE_STATES):
            try:
                await self._advance_one(task)
            except Exception as exc:                     # isolate per-task failures too
                log.exception("advance failed for %s", task.id)
                await self._audit(task.id, "advance_error", str(exc))

    async def _advance_one(self, task: Task) -> None:
        if task.state is TaskState.PENDING:
            deps = [await self.board.get(d) for d in task.depends_on]
            if any(d is None or d.state in (TaskState.FAILED, TaskState.REJECTED,
                                            TaskState.CANCELLED) for d in deps):
                await self._set_state(task, TaskState.CANCELLED, "dependency failed")
            elif all(d is not None and d.state is TaskState.SUCCEEDED for d in deps):
                await self._gate(task)
        elif task.state is TaskState.AWAITING_APPROVAL:
            await self._gate(task)
        elif task.state is TaskState.LEASED:
            if task.lease_expires is not None and task.lease_expires < now():
                await self._retry_or_fail(task, "lease expired")

    async def _gate(self, task: Task) -> None:
        """Approval / quorum gate between PENDING and READY."""
        rule = self.cfg.approval_policy.get(task.spec.risk)
        if rule is None or not rule.required:
            await self._set_state(task, TaskState.READY, "no approval required")
            return
        fresh = [a for a in task.approvals
                 if now() - a.ts < timedelta(seconds=self.cfg.approval_ttl_s)]
        latest: dict[str, Approval] = {}
        for a in sorted(fresh, key=lambda a: a.ts):
            latest[a.approver] = a                       # ONE vote per approver, latest wins
        if rule.veto and any(a.decision == "reject" for a in latest.values()):
            self.metrics["rejected"] += 1
            await self._set_state(task, TaskState.REJECTED, "quorum veto")
            return
        counts: dict[str, int] = {}
        for a in latest.values():
            if a.decision == "approve":
                counts[a.role] = counts.get(a.role, 0) + 1
        if all(counts.get(role, 0) >= n for role, n in rule.required.items()):
            await self._set_state(task, TaskState.READY, "quorum met")
        elif task.state is not TaskState.AWAITING_APPROVAL:
            await self._set_state(task, TaskState.AWAITING_APPROVAL,
                                  f"needs {rule.required}")

    async def _retry_or_fail(self, task: Task, reason: str) -> None:
        task.lease_holder = None
        task.lease_expires = None
        if task.attempts < self.cfg.max_attempts:
            delay = min(self.cfg.backoff_base_s * (2 ** max(task.attempts - 1, 0)),
                        self.cfg.backoff_cap_s)
            task.not_before = now() + timedelta(seconds=delay)
            self.metrics["retries"] += 1
            await self._set_state(task, TaskState.READY,
                                  f"{reason}; retry {task.attempts + 1}/{self.cfg.max_attempts} "
                                  f"in {delay:.0f}s")
        else:
            task.error = task.error or reason
            await self._set_state(task, TaskState.FAILED,
                                  f"{reason}; attempts exhausted")

    # ------------------------------------------------------------ helpers
    async def _check_holder(self, task_id: str, worker: str) -> Task:
        task = await self.board.get(task_id)
        if task is None:
            raise KeyError(f"unknown task {task_id}")
        if task.state is not TaskState.LEASED or task.lease_holder != worker:
            raise PermissionError(f"{worker} does not hold lease on {task_id}")
        if task.lease_expires is None or task.lease_expires <= now():
            raise PermissionError(f"lease on {task_id} expired; {worker} is fenced off")
        return task

    async def _set_state(self, task: Task, state: TaskState, reason: str) -> None:
        old = task.state
        task.state = state
        task.updated_at = now()
        await self.board.save(task)
        await self._audit(task.id, "state", f"{old.value} -> {state.value}: {reason}")
        log.info("task %s %s -> %s (%s)", task.id, old.value, state.value, reason)

    async def _audit(self, task_id: Optional[str], action: str, detail: str) -> None:
        await self.board.append(Event(kind=EventKind.AUDIT, source="queen",
                                      payload={"task_id": task_id, "action": action,
                                               "detail": detail}))

    async def snapshot(self) -> list[Task]:
        return await self.board.find()


Queen = QueenCoordinator   # shorter alias for callers