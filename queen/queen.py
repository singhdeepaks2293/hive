"""queen/queen.py -- the Queen puts chaos into order.

    inputs  syslog / traps (ingest), Sentinel health (report), humans (request, vote,
            freeze), intent drones (publish_desired), drone results (complete / fail)
    tick    1. leader lease          only one Queen acts; any other stands by
            2. inbox -> order        dedup, storm, correlate by failure domain
                     -> classify     needs, as skills
                     -> dispatch     directives for the right drones
            3. consent + governance  PR approvals, votes, freeze, windows, locks, canary
            4. registry upkeep       regenerate severed drones
            5. schedule              assign READY directives, reap leases
            6. drift + mapping       observe, compare, raise drift, map unknown domains
            7. housekeeping          close quiet incidents

The Queen keeps no state of her own; everything is on the blackboard. She
never touches a device, never interprets protocol details, and never approves
a change. Drones never create work: their results can carry facts, findings
or (for proposer skills only) a proposal, and the Queen decides what follows.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from .audit import Audit
from .blackboard import Blackboard
from .classify import Classifier, Need, RuleClassifier
from .consent import (ApprovalSource, Authenticator, ConsentError, ConsentGate,
                      ConsentPolicy, Principal)
from .dispatch import Dispatcher, Verdict
from .drift import DriftWatch
from .governance import Picture, may_start, rollout_failure
from .models import (DEAD_DIRECTIVE, OPEN_CHANGE, Change, ChangeState,
                     DesiredState, DeviceDomains, Directive, DroneManifest,
                     Governance,
                     Incident, QueenConfig, Risk, Signal, SignalKind, utcnow)
from .order import Orderer
from .registry import Registry, skill_score

log = logging.getLogger("hive.queen")

PROPOSER_SKILLS = {"drift.remediate", "config.propose"}
REPORTER_SKILLS = ("health", "collect")      # drones with these may raise signals
MAX_FINDINGS = 20


class NotLeader(Exception):
    """This Queen is standing by. Call the leader."""


class Queen:
    def __init__(self, board: Blackboard, *, authenticator: Authenticator,
                 name: str = "queen", classifier: Optional[Classifier] = None,
                 config: Optional[QueenConfig] = None,
                 consent_policy: Optional[ConsentPolicy] = None,
                 approvals: Optional[ApprovalSource] = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.board = board
        self.name = name
        self.cfg = config or QueenConfig()
        self.clock = clock
        self.auth = authenticator
        self.classifier = classifier or RuleClassifier()
        self.audit = Audit(board, clock, name)
        self.orderer = Orderer(board, self.cfg, clock)
        self.registry = Registry(board, self.cfg, clock, self.audit)
        self.dispatch = Dispatcher(board, self.cfg, clock, self.audit, self.registry,
                                   self._consent_verdict)
        self.drift = DriftWatch(board, self.cfg, clock, self.audit)
        self.gate = ConsentGate(consent_policy or ConsentPolicy(), clock, authenticator,
                                approvals)
        self._lock = asyncio.Lock()
        self._leading = False
        self._halted = False                     # stopped / crashed: refuse everything
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()

    # ================================================================== leadership
    async def _lead(self) -> bool:
        if self._halted:
            self._leading = False
            return False
        ok = await self.board.acquire_lease("queen", self.name, self.cfg.leader_ttl_s,
                                            self.clock())
        if ok and not self._leading:
            await self.audit("queen_ascended", self.name, "the collective has a Queen")
        self._leading = ok
        return ok

    async def _require_leader(self) -> None:
        if not await self._lead():
            raise NotLeader(f"{self.name} is standing by")

    @property
    def leading(self) -> bool:
        return self._leading

    # ================================================================== inputs
    async def ingest(self, signal: Signal) -> Signal:
        """Syslog / trap receivers call this. Any Queen accepts input."""
        if signal.kind not in (SignalKind.SYSLOG, SignalKind.TRAP):
            raise ValueError("ingest() takes syslog or traps; use request() or report()")
        signal.principal = None                          # events never carry identity
        return await self._enqueue(signal)

    async def request(self, token: str, text: str, device: Optional[str] = None,
                      **payload: Any) -> Signal:
        principal = self._authenticate(token)
        sig = Signal(kind=SignalKind.REQUEST, source=principal.name, principal=principal.name,
                     text=text, device=device, payload=payload)
        return await self._enqueue(sig)

    async def report(self, designation: str, signal: Signal) -> Signal:
        """A Sentinel drone reports a health deviation."""
        drone = await self.registry.get(designation)
        if drone is None or not any(s.split(".")[0] in REPORTER_SKILLS
                                    for s in drone.manifest.skills):
            raise PermissionError(f"{designation} may not raise signals")
        if signal.kind is not SignalKind.HEALTH or not signal.device:
            raise ValueError("drones report HEALTH signals about a device")
        signal.source, signal.principal = designation, None
        return await self._enqueue(signal)

    async def _enqueue(self, sig: Signal) -> Signal:
        if self._halted:
            raise NotLeader(f"{self.name} is down")
        sig.received_at = self.clock()
        await self.board.put("inbox", sig.id, sig)
        self._wake.set()
        return sig

    def _authenticate(self, token: str) -> Principal:
        principal = self.auth.verify(token)
        if principal is None:
            raise ConsentError("unauthenticated")
        return principal

    # ================================================================== humans
    async def vote(self, change_id: str, token: str, decision: str, content_hash: str,
                   note: str = "") -> Change:
        async with self._lock:
            await self._require_leader()
            principal = self._authenticate(token)
            change = await self._change(change_id)
            try:
                self.gate.vote(change, principal, decision, content_hash, note)
            except ConsentError as exc:
                await self.audit("vote_refused", change_id, f"{principal.name}: {exc}")
                raise
            await self.audit("vote", change_id, f"{principal.name} {decision} {note}".strip())
            reason = self.gate.evaluate(change)
            await self.board.put("changes", change.id, change)
            if reason:
                await self.audit("change_state", change.id, f"{change.state.value}: {reason}")
            self._wake.set()
            return change

    async def freeze(self, token: str, reason: str) -> Governance:
        return await self._set_freeze(token, True, reason)

    async def unfreeze(self, token: str) -> Governance:
        return await self._set_freeze(token, False, "")

    async def _set_freeze(self, token: str, frozen: bool, reason: str) -> Governance:
        async with self._lock:
            await self._require_leader()
            principal = self._authenticate(token)
            if self.gate.policy.approver_role not in principal.roles:
                raise ConsentError(f"{principal.name} may not operate the freeze switch")
            gov = Governance(frozen=frozen, by=principal.name, reason=reason,
                             since=self.clock() if frozen else None)
            await self.board.put("governance", "global", gov)
            await self.audit("freeze" if frozen else "unfreeze", "collective",
                             f"{principal.name}: {reason}".strip(": "))
            self._wake.set()
            return gov

    # ================================================================== drones
    async def register(self, manifest: DroneManifest):
        async with self._lock:
            await self._require_leader()
            return await self.registry.register(manifest)

    async def heartbeat(self, designation: str) -> None:
        async with self._lock:
            await self._require_leader()
            await self.registry.touch(designation)

    async def receive(self, designation: str) -> Optional[Directive]:
        async with self._lock:
            await self._require_leader()
            await self.registry.touch(designation)
            return await self.dispatch.receive(designation)

    async def renew(self, directive_id: str, designation: str) -> bool:
        async with self._lock:
            await self._require_leader()
            return await self.dispatch.renew(directive_id, designation)

    async def complete(self, directive_id: str, designation: str,
                       result: Optional[dict[str, Any]] = None) -> Directive:
        async with self._lock:
            await self._require_leader()
            d = await self.dispatch.complete(directive_id, designation, result or {})
            try:
                await self._after_success(d, designation)
            except Exception as exc:                     # a bad result must not kill the hive
                log.exception("result of %s rejected", d.id)
                await self.audit("result_rejected", d.id, str(exc))
            self._wake.set()
            return d

    async def fail(self, directive_id: str, designation: str, error: str,
                   retryable: bool = True) -> Directive:
        async with self._lock:
            await self._require_leader()
            d = await self.dispatch.fail(directive_id, designation, error, retryable)
            self._wake.set()
            return d

    async def publish_desired(self, designation: str, device: str, config: dict[str, Any],
                              source: str, version: str, strict: bool = False):
        async with self._lock:
            await self._require_leader()
            drone = await self.registry.get(designation)
            if drone is None or skill_score(drone.manifest.skills, "intent.publish") < 0:
                raise PermissionError(f"{designation} may not publish intent")
            state = await self.drift.publish_desired(device, config, source, version,
                                                     designation, strict)
            self._wake.set()
            return state

    # ================================================================== views
    async def incident(self, incident_id: str) -> Optional[Incident]:
        return await self.board.get("incidents", incident_id, Incident)

    async def changes(self) -> list[Change]:
        return sorted(await self.board.scan("changes", Change), key=lambda c: c.created_at)

    async def incidents(self) -> list[Incident]:
        return sorted(await self.board.scan("incidents", Incident), key=lambda i: i.first_seen)

    async def directives(self) -> list[Directive]:
        return sorted(await self.dispatch.all(), key=lambda d: d.created_at)

    async def governance(self) -> Governance:
        return await self.board.get("governance", "global", Governance) or Governance()

    # ================================================================== lifecycle
    async def start(self) -> None:
        self._halted = False
        self._running = True
        self._loop_task = asyncio.create_task(self.run_forever(), name=self.name)

    async def stop(self, release: bool = True) -> None:
        """release=False simulates a crash: the lease expires on its own."""
        self._running = False
        self._halted = True
        self._wake.set()
        if self._loop_task:
            await self._loop_task
            self._loop_task = None
        if release and self._leading:
            await self.board.release_lease("queen", self.name)
            await self.audit("queen_stepped_down", self.name)
        self._leading = False

    async def run_forever(self) -> None:
        while self._running:
            self._wake.clear()
            try:
                await self.tick()
            except Exception:                            # the loop itself never dies
                log.exception("tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.tick_s)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> bool:
        """One pass. Returns False when standing by. Tests drive this directly."""
        async with self._lock:
            if not await self._lead():
                return False
            await self._absorb_inbox()
            await self._consent_pass()
            await self.registry.upkeep()
            await self.dispatch.schedule()
            await self._drift_pass()
            await self._mapping_pass()
            for inc in await self.orderer.housekeeping(
                    {d.id for d in await self.dispatch.active()}):
                await self.audit("incident_closed", inc.id,
                                 f"{inc.key}: {inc.signal_count} signals, "
                                 f"{inc.suppressed} suppressed")
            return True

    # ================================================================== order
    async def _absorb_inbox(self) -> None:
        inbox = sorted(await self.board.scan("inbox", Signal), key=lambda s: s.received_at)
        for sig in inbox:
            try:
                await self._order(sig)
            except Exception as exc:                     # one bad signal must not stop the rest
                log.exception("signal %s failed", sig.id)
                await self.audit("signal_error", sig.id, str(exc))
            finally:
                await self.board.append("signal_log", sig)
                await self.board.delete("inbox", sig.id)

    async def _order(self, sig: Signal) -> None:
        domains = (await self.board.get("domains", sig.device, DeviceDomains)
                   if sig.device else None)
        ab = await self.orderer.absorb(sig, domains)
        inc = ab.incident
        if ab.new_incident:
            what = sig.mnemonic or sig.payload.get("check") or sig.text[:60]
            await self.audit("incident_opened", inc.id, f"{inc.key} <- {sig.kind.value} {what}")
        if ab.storm_started:
            await self._storm(inc)
        if ab.suppressed:
            return
        needs = self.classifier.plan(inc, sig)
        await self._enact(inc, sig, needs, domains)

    async def _enact(self, inc: Incident, sig: Signal, needs: list[Need],
                     domains: Optional[DeviceDomains]) -> None:
        ids: list[str] = []
        for need in needs:
            existing = inc.skills.get(need.skill)
            if existing:
                ex = await self.dispatch.get(existing)
                if ex is not None and ex.state not in DEAD_DIRECTIVE:
                    ids.append(ex.id)
                    continue
            prio = min(need.priority if need.priority is not None else inc.priority,
                       inc.priority)
            d, _ = await self.dispatch.create(
                need.skill, device=need.device or sig.device, params=need.params,
                priority=prio, incident_id=inc.id, depends_on=[ids[i] for i in need.after],
                platform=domains.platform if domains else None)
            inc.skills[need.skill] = d.id
            ids.append(d.id)
        await self.board.put("incidents", inc.id, inc)

    async def _storm(self, inc: Incident) -> None:
        await self.audit("storm", inc.id, f"{inc.key}: escalated to priority 0")
        for did in inc.skills.values():
            await self.dispatch.escalate(did, 0)
        if not any(s.startswith("tshoot") for s in inc.skills):
            await self._enact(inc, Signal(kind=SignalKind.SYSLOG, device=inc.devices[0]
                                          if inc.devices else None),
                              [Need(skill="tshoot", priority=0,
                                    params={"focus": "event_storm", "incident": inc.key})],
                              None)

    # ================================================================== results
    async def _after_success(self, d: Directive, designation: str) -> None:
        result = d.result or {}

        observed = result.get("observed")
        if observed is not None and d.device and (d.skill.startswith("collect.")
                                                  or d.skill == "config.apply"):
            if not isinstance(observed, dict):
                raise ValueError("observed must be a key -> value dict")
            await self.drift.record_observed(d.device, observed, designation)

        if d.skill == "topology.map" and d.device and isinstance(result.get("domains"), dict):
            dom = DeviceDomains(device=d.device,
                                domains={str(k): str(v) for k, v in result["domains"].items()},
                                ports={str(k): str(v) for k, v in
                                       (result.get("ports") or {}).items()},
                                platform=result.get("platform"), mapped_by=designation,
                                mapped_at=self.clock())
            await self.board.put("domains", d.device, dom)
            await self.audit("domains_mapped", d.device, str(dom.domains))

        if d.skill.startswith("tshoot") and result.get("findings") and d.incident_id:
            inc = await self.incident(d.incident_id)
            if inc is not None:
                for f in result["findings"][:MAX_FINDINGS]:
                    inc.findings.append({"by": designation, "skill": d.skill, **f})
                inc.findings = inc.findings[-MAX_FINDINGS:]
                await self.board.put("incidents", inc.id, inc)
                top = result["findings"][0]
                await self.audit("finding", inc.id, f"{top.get('summary', '')} "
                                 f"(confidence {top.get('confidence', '?')})")

        if "pull_request" in result:
            await self.audit("pull_request_opened", d.id, str(result["pull_request"]))

        if "proposal" in result:
            if d.skill not in PROPOSER_SKILLS:
                await self.audit("proposal_refused", d.id, f"{d.skill} may not propose changes")
            else:
                await self._open_change(d, designation, result["proposal"])

        if d.skill == "verify.change" and d.change_id:
            change = await self._change(d.change_id)
            passed = bool(result.get("passed")) and result.get("content_hash") == change.content_hash
            self.gate.record_verification(change, passed, result.get("evidence", {}),
                                          list(result.get("rollback", [])))
            await self.audit("verified", change.id, "passed" if passed else
                             f"failed: {result.get('evidence', {}).get('problems', '?')}")
            reason = self.gate.evaluate(change)
            await self.board.put("changes", change.id, change)
            if reason:
                await self.audit("change_state", change.id, f"{change.state.value}: {reason}")

        if d.skill == "config.apply" and d.change_id:
            change = await self._change(d.change_id)
            change.state, change.applied_at = ChangeState.APPLIED, self.clock()
            change.reason = f"applied by {designation}"
            change.updated_at = self.clock()
            await self.board.put("changes", change.id, change)
            await self.audit("change_applied", change.id, f"{change.device} by {designation}")

    async def _open_change(self, d: Directive, designation: str, proposal: Any) -> None:
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be a dict")
        device = str(proposal.get("device") or d.device or "")
        if d.device and device != d.device:
            raise ValueError(f"proposal for {device} from a directive about {d.device}")
        config = proposal.get("config", "")
        if isinstance(config, list):
            config = "\n".join(str(x) for x in config)
        source = str(proposal.get("source", ""))
        if not device or not source:
            raise ValueError("proposal needs device and source")
        requested_by = d.params.get("requested_by")
        break_glass = (bool(proposal.get("break_glass")) and d.skill == "config.propose"
                       and requested_by is not None)
        change = self.gate.open(device=device, config=str(config), source=source,
                                summary=str(proposal.get("summary", "")),
                                proposed_by=designation, requested_by=requested_by,
                                incident_id=d.incident_id, break_glass=break_glass,
                                rollout=source if source.startswith("git:") else None)
        for other in await self.board.scan("changes", Change):
            if other.state in OPEN_CHANGE and other.content_hash == change.content_hash:
                await self.audit("proposal_duplicate", other.id, f"from {d.id}")
                return
        await self.board.put("changes", change.id, change)
        await self.audit("change_proposed", change.id,
                         f"{device} from {source}{' BREAK-GLASS' if break_glass else ''}: "
                         f"{change.summary}")
        domains = await self.board.get("domains", device, DeviceDomains)
        v, _ = await self.dispatch.create(
            "verify.change", device=device, priority=1, incident_id=d.incident_id,
            change_id=change.id,
            params={"change_id": change.id, "content_hash": change.content_hash,
                    "config": change.config, "source": source, "break_glass": break_glass,
                    "domains": domains.domains if domains else {}})
        change.verify_directive_id = v.id
        await self.board.put("changes", change.id, change)

    async def _change(self, change_id: str) -> Change:
        change = await self.board.get("changes", change_id, Change)
        if change is None:
            raise KeyError(f"unknown change {change_id}")
        return change

    # ================================================================== consent + governance
    async def _consent_verdict(self, change_id: str) -> Verdict:
        change = await self.board.get("changes", change_id, Change)
        if change is None or change.state is not ChangeState.APPLYING:
            return "cancel"
        if (await self.governance()).frozen:
            return "wait"
        return "ok"

    async def _consent_pass(self) -> None:
        changes = await self.board.scan("changes", Change)
        by_id = {d.id: d for d in await self.dispatch.all()}

        for c in changes:
            before = (c.state, c.reason)
            if c.state in (ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL,
                           ChangeState.APPROVED):
                halted = rollout_failure(c, changes)
                if halted is not None:
                    c.state = ChangeState.REJECTED
                    c.reason = f"rollout halted: change {halted.id} on {halted.device} failed"
            if c.state is ChangeState.VERIFYING:
                v = by_id.get(c.verify_directive_id or "")
                if v is not None and v.state in DEAD_DIRECTIVE:
                    c.verified = False
                    c.verification = {"problems": [f"verification {v.state.value}: {v.error}"]}
            if c.state in (ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL):
                if self.gate.import_pr_approvals(c):
                    await self.audit("pr_approvals", c.id, ", ".join(
                        v.principal for v in c.votes if v.principal.startswith("git:")))
                self.gate.evaluate(c)
            if c.state is ChangeState.APPLYING:
                a = by_id.get(c.apply_directive_id or "")
                if a is not None and a.state in DEAD_DIRECTIVE:
                    c.state = ChangeState.FAILED
                    c.reason = f"apply {a.state.value}: {a.error or 'no reason'}"
            if (c.state, c.reason) != before:
                c.updated_at = self.clock()
                await self.board.put("changes", c.id, c)
                await self.audit("change_state", c.id, f"{c.state.value}: {c.reason}")

        pic = Picture(governance=await self.governance(), changes=changes,
                      domains={d.device: d for d in await self.board.scan("domains",
                                                                         DeviceDomains)},
                      open_fault_incidents={i.domain: i.id for i in
                                            await self.board.scan("incidents", Incident)
                                            if i.lane == "fault" and not i.closed and i.domain})
        now = self.clock()
        for c in sorted((c for c in changes if c.state is ChangeState.APPROVED),
                        key=lambda c: (not c.break_glass, c.created_at)):
            ok, why = may_start(c, pic, self.cfg, now)
            if not ok:
                if c.reason != why:
                    c.reason, c.updated_at = why, now
                    await self.board.put("changes", c.id, c)
                    await self.audit("change_waiting", c.id, why)
                continue
            c.state, c.reason, c.updated_at = ChangeState.APPLYING, why, now
            a, _ = await self.dispatch.create(
                "config.apply", device=c.device, priority=0 if c.break_glass else 1,
                risk=Risk.CHANGE, incident_id=c.incident_id, change_id=c.id,
                params={"change_id": c.id, "content_hash": c.content_hash,
                        "config": c.config, "rollback": c.rollback})
            c.apply_directive_id = a.id
            await self.board.put("changes", c.id, c)
            await self.audit("change_started", c.id, f"{c.device} ({why})")

    # ================================================================== drift + mapping
    async def _drift_pass(self) -> None:
        changes = await self.board.scan("changes", Change)
        hold = {c.device for c in changes if c.state in OPEN_CHANGE}
        # a halted rollout needs a human: a new intent commit (fix or revert) restarts it
        halted = {c.rollout for c in changes if c.rollout and c.state is ChangeState.FAILED}
        hold |= {c.device for c in changes if c.rollout in halted}
        result = await self.drift.sweep(hold)
        active = await self.dispatch.active()
        for dev in result.collect:
            if not any(d.skill == "collect.config" and d.device == dev for d in active):
                await self.dispatch.create("collect.config", device=dev, priority=3,
                                           params={"reason": "observe for drift"})
        for report in result.drifted:
            n = len(report.missing) + len(report.changed) + len(report.unexpected)
            await self._enqueue(Signal(
                kind=SignalKind.DRIFT, source=self.name, device=report.device,
                text=f"{n} setting(s) differ from {report.source}",
                payload={"drift": report.model_dump(), "drift_hash": report.digest,
                         "desired_version": report.desired_version, "source": report.source}))
            await self.drift.note_signaled(report)
            await self.audit("drift_detected", report.device,
                             f"{n} setting(s) differ from {report.source}")

    async def _mapping_pass(self) -> None:
        """Every device with intent must have known failure domains."""
        mapped = {d.device for d in await self.board.scan("domains", DeviceDomains)}
        active = await self.dispatch.active()
        for desired in await self.board.scan("desired", DesiredState):
            dev = desired.device
            if dev in mapped or any(d.skill == "topology.map" and d.device == dev
                                    for d in active):
                continue
            await self.dispatch.create("topology.map", device=dev, priority=2,
                                       params={"reason": "failure domains unknown"})
