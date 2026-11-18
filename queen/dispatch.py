"""queen/dispatch.py -- the Queen commands; drones obey.

Lifecycle:
    PENDING --deps ok--> READY --Queen picks drone--> ASSIGNED --drone receives--> LEASED
    LEASED --complete--> SUCCEEDED
    LEASED --fail / lease expiry--> READY (backoff, prefer another drone) or FAILED

Routing rule for each READY directive, in order:
    1. drone must be ACTIVE, alive, platform-compatible, under capacity
    2. most specific skill wins (tshoot.rep beats tshoot)
    3. on retry, a drone that has not tried it yet wins
    4. least loaded wins, then designation for determinism

CHANGE directives (config.apply) are never retried: a half-applied change is
not something to repeat blindly. Before assigning one, and again when the
drone picks it up, the dispatcher asks the Queen whether the change may still
go ahead ("ok"), must wait ("wait", e.g. freeze switch), or is dead ("cancel").
That check is defense in depth on top of the consent gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Literal, Optional

from .audit import Audit
from .blackboard import Blackboard
from .models import (ACTIVE_DIRECTIVE, DEAD_DIRECTIVE, Directive, DirectiveState, Drone,
                     QueenConfig, Risk)
from .registry import Registry

Verdict = Literal["ok", "wait", "cancel"]
ConsentCheck = Callable[[str], Awaitable[Verdict]]   # change_id -> may this be applied now?


class Dispatcher:
    def __init__(self, board: Blackboard, cfg: QueenConfig, clock: Callable[[], datetime],
                 audit: Audit, registry: Registry, consent_ok: ConsentCheck) -> None:
        self.board = board
        self.cfg = cfg
        self.clock = clock
        self.audit = audit
        self.registry = registry
        self.consent_ok = consent_ok

    # ------------------------------------------------------------------ creation
    async def create(self, skill: str, *, device: Optional[str] = None,
                     params: Optional[dict[str, Any]] = None, priority: int = 2,
                     risk: Risk = Risk.READ_ONLY, incident_id: Optional[str] = None,
                     change_id: Optional[str] = None, depends_on: Optional[list[str]] = None,
                     platform: Optional[str] = None) -> tuple[Directive, bool]:
        """Returns (directive, created). An active twin is reused, not duplicated."""
        now = self.clock()
        d = Directive(skill=skill, device=device, params=params or {}, priority=priority,
                      risk=risk, incident_id=incident_id, change_id=change_id,
                      depends_on=depends_on or [], platform=platform,
                      created_at=now, updated_at=now)
        key = d.dedup_key()
        for other in await self.active():
            if other.dedup_key() == key:
                if priority < other.priority:            # escalate, never de-escalate
                    other.priority = priority
                    await self._save(other)
                await self.audit("directive_reused", other.id, f"skill={skill} device={device}")
                return other, False
        await self._save(d)
        await self.audit("directive_created", d.id,
                         f"skill={skill} device={device} prio={priority} risk={risk.value}")
        return d, True

    async def get(self, directive_id: str) -> Optional[Directive]:
        return await self.board.get("directives", directive_id, Directive)

    async def all(self) -> list[Directive]:
        return await self.board.scan("directives", Directive)

    async def active(self) -> list[Directive]:
        return [d for d in await self.all() if d.state in ACTIVE_DIRECTIVE]

    async def escalate(self, directive_id: str, priority: int) -> None:
        d = await self.get(directive_id)
        if d and d.state in ACTIVE_DIRECTIVE and priority < d.priority:
            d.priority = priority
            await self._save(d)

    # ------------------------------------------------------------------ scheduling
    async def schedule(self) -> None:
        now = self.clock()
        directives = await self.all()
        by_id = {d.id: d for d in directives}

        # deps, assignment timeouts, lease expiry
        for d in directives:
            if d.state is DirectiveState.PENDING:
                deps = [by_id.get(x) for x in d.depends_on]
                if any(x is None or x.state in DEAD_DIRECTIVE for x in deps):
                    await self._set(d, DirectiveState.CANCELLED, "dependency failed")
                elif all(x.state is DirectiveState.SUCCEEDED for x in deps):
                    await self._set(d, DirectiveState.READY, "dependencies met")
            elif d.state is DirectiveState.ASSIGNED:
                if now - d.assigned_at > timedelta(seconds=self.cfg.assign_timeout_s):
                    drone = d.assigned_to
                    d.assigned_to, d.assigned_at = None, None
                    await self._set(d, DirectiveState.READY, f"{drone} never picked it up")
                    await self.registry.record_failure(drone, "missed assignment")
            elif d.state is DirectiveState.LEASED:
                if d.lease_expires is not None and d.lease_expires <= now:
                    holder = d.assigned_to
                    await self.registry.record_failure(holder, "lease expired")
                    await self._retry_or_fail(d, f"lease expired on {holder}")

        # assignment
        directives = await self.all()
        drones = await self.registry.all()
        load: dict[str, int] = {}
        dev_load: dict[str, int] = {}
        dev_change: set[str] = set()
        for d in directives:
            if d.state in (DirectiveState.ASSIGNED, DirectiveState.LEASED):
                load[d.assigned_to] = load.get(d.assigned_to, 0) + 1
                if d.device:
                    dev_load[d.device] = dev_load.get(d.device, 0) + 1
                    if d.risk is Risk.CHANGE:
                        dev_change.add(d.device)

        ready = sorted((d for d in directives if d.state is DirectiveState.READY
                        and (d.not_before is None or d.not_before <= now)),
                       key=lambda d: (d.priority, d.created_at))
        for d in ready:
            if d.device:
                if d.device in dev_change:
                    continue                             # a change owns the device right now
                if d.risk is Risk.CHANGE and dev_load.get(d.device, 0) > 0:
                    continue                             # changes need the device to themselves
                if dev_load.get(d.device, 0) >= self.cfg.device_concurrency:
                    continue
            if d.risk is Risk.CHANGE:
                verdict = await self.consent_ok(d.change_id) if d.change_id else "cancel"
                if verdict == "cancel":
                    await self._set(d, DirectiveState.CANCELLED, "consent not in force")
                    continue
                if verdict == "wait":
                    continue
            drone = self._pick(d, drones, load)
            if drone is None:
                await self._unroutable(d, drones)
                continue
            d.unroutable_since = None
            d.assigned_to, d.assigned_at = drone.designation, now
            await self._set(d, DirectiveState.ASSIGNED, f"to {drone.designation}")
            load[drone.designation] = load.get(drone.designation, 0) + 1
            if d.device:
                dev_load[d.device] = dev_load.get(d.device, 0) + 1
                if d.risk is Risk.CHANGE:
                    dev_change.add(d.device)

    def _pick(self, d: Directive, drones: list[Drone], load: dict[str, int]) -> Optional[Drone]:
        options = [(score, drone) for score, drone in
                   self.registry.eligible(drones, d.skill, d.platform)
                   if load.get(drone.designation, 0) < drone.manifest.capacity]
        if not options:
            return None
        options.sort(key=lambda sd: (-sd[0],                          # most specific skill
                                     sd[1].designation in d.tried_by,  # fresh eyes on retry
                                     load.get(sd[1].designation, 0),
                                     sd[1].designation))
        return options[0][1]

    async def _unroutable(self, d: Directive, drones: list[Drone]) -> None:
        now = self.clock()
        if d.unroutable_since is None:
            d.unroutable_since = now
            await self._save(d)
            anyone = any(self.registry.eligible(drones, d.skill, d.platform))
            await self.audit("directive_waiting", d.id,
                             f"skill={d.skill}: " + ("all capable drones busy" if anyone
                                                     else "no drone in the collective has it"))
        elif now - d.unroutable_since > timedelta(seconds=self.cfg.unroutable_timeout_s):
            d.error = f"no drone served skill {d.skill} in {self.cfg.unroutable_timeout_s:.0f}s"
            await self._set(d, DirectiveState.FAILED, d.error)

    # ------------------------------------------------------------------ drone side
    async def receive(self, designation: str) -> Optional[Directive]:
        mine = sorted((d for d in await self.all()
                       if d.state is DirectiveState.ASSIGNED and d.assigned_to == designation),
                      key=lambda d: (d.priority, d.created_at))
        if not mine:
            return None
        d = mine[0]
        if d.risk is Risk.CHANGE:
            verdict = await self.consent_ok(d.change_id) if d.change_id else "cancel"
            if verdict != "ok":
                # freeze or withdrawn consent: hand it back (or kill it) before anything runs
                d.assigned_to, d.assigned_at = None, None
                state = DirectiveState.CANCELLED if verdict == "cancel" else DirectiveState.READY
                await self._set(d, state, f"consent says {verdict} at pickup")
                return None
        d.attempts += 1
        d.tried_by.append(designation)
        d.lease_expires = self.clock() + timedelta(seconds=self.cfg.lease_ttl_s)
        await self._set(d, DirectiveState.LEASED, f"received by {designation}")
        return d

    async def renew(self, directive_id: str, designation: str) -> bool:
        d = await self.get(directive_id)
        if (d is None or d.state is not DirectiveState.LEASED or d.assigned_to != designation
                or d.lease_expires is None or d.lease_expires <= self.clock()):
            return False                                 # lost the lease: the drone must stop
        d.lease_expires = self.clock() + timedelta(seconds=self.cfg.lease_ttl_s)
        await self._save(d)
        return True

    async def complete(self, directive_id: str, designation: str,
                       result: dict[str, Any]) -> Directive:
        d = await self._holder(directive_id, designation)
        d.result = result
        await self._set(d, DirectiveState.SUCCEEDED, f"completed by {designation}")
        await self.registry.record_success(designation)
        return d

    async def fail(self, directive_id: str, designation: str, error: str,
                   retryable: bool = True) -> Directive:
        d = await self._holder(directive_id, designation)
        d.error = error
        await self.registry.record_failure(designation, error)
        if retryable:
            await self._retry_or_fail(d, f"{designation}: {error}")
        else:
            d.lease_expires = None
            await self._set(d, DirectiveState.FAILED, f"{designation}: {error} (not retryable)")
        return d

    async def cancel(self, directive_id: str, reason: str) -> None:
        d = await self.get(directive_id)
        if d and d.state in ACTIVE_DIRECTIVE:
            await self._set(d, DirectiveState.CANCELLED, reason)

    # ------------------------------------------------------------------ helpers
    async def _retry_or_fail(self, d: Directive, reason: str) -> None:
        d.lease_expires = None
        d.assigned_to, d.assigned_at = None, None
        if d.risk is Risk.CHANGE:
            d.error = d.error or reason
            await self._set(d, DirectiveState.FAILED, f"{reason}; changes are never retried")
            return
        if d.attempts < self.cfg.max_attempts:
            delay = min(self.cfg.backoff_base_s * 2 ** max(d.attempts - 1, 0),
                        self.cfg.backoff_cap_s)
            d.not_before = self.clock() + timedelta(seconds=delay)
            await self._set(d, DirectiveState.READY,
                            f"{reason}; retry {d.attempts + 1}/{self.cfg.max_attempts} "
                            f"in {delay:.0f}s")
        else:
            d.error = d.error or reason
            await self._set(d, DirectiveState.FAILED, f"{reason}; attempts exhausted")

    async def _holder(self, directive_id: str, designation: str) -> Directive:
        d = await self.get(directive_id)
        if d is None:
            raise KeyError(f"unknown directive {directive_id}")
        if d.state is not DirectiveState.LEASED or d.assigned_to != designation:
            raise PermissionError(f"{designation} does not hold {directive_id}")
        if d.lease_expires is None or d.lease_expires <= self.clock():
            raise PermissionError(f"lease on {directive_id} expired; {designation} is fenced off")
        return d

    async def _set(self, d: Directive, state: DirectiveState, reason: str) -> None:
        old = d.state
        d.state = state
        await self._save(d)
        await self.audit("directive_state", d.id,
                         f"{d.skill}: {old.value} -> {state.value} ({reason})")

    async def _save(self, d: Directive) -> None:
        d.updated_at = self.clock()
        await self.board.put("directives", d.id, d)
