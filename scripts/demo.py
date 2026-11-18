"""scripts/demo.py -- the collective at work on a simulated plant ring.

    python scripts/demo.py            (from the repo root)

    Act 1  assimilation   map domains, observe config, find drift on ie-sw2
    Act 2  chaos          a ring link fails and a port flaps: dozens of signals
    Act 3  order          signals become incidents, routed to the right drones
    Act 4  consent        approvals with real identities; the change waits for the ring
    Act 5  gitops         a request becomes a PR; merge rolls out canary-first
                          through a freeze and a Queen failover
    Act 6  report

No hardware: netskills.fake simulates the switches, hive_lab wires it up.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hive_lab import build_lab                                        # noqa: E402
from netskills.gateway import gateway_summary                         # noqa: E402
from queen import ChangeState, ConsentError                           # noqa: E402
from queen.audit import STREAM                                         # noqa: E402
from queen.models import AuditRecord, Signal                          # noqa: E402

T0 = time.monotonic()


def say(text: str = "") -> None:
    print(f"[{time.monotonic() - T0:5.1f}s] {text}" if text else "")


def act(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


async def until(cond: Callable[[], Awaitable[bool]], timeout: float = 15.0,
                what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await cond():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {what}")


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    lab = build_lab()
    q = lab.link
    await lab.start()

    # ------------------------------------------------------------------ act 1
    act("ACT 1  Assimilation: map, observe, compare against intent")
    say(f"{lab.leader.name} leads; {len(lab.drones)} drones joined; "
        f"intent {lab.repo.source()}")

    async def drift_change():
        return [c for c in await q.changes() if c.device == "ie-sw2"]
    await until(lambda: _any(drift_change, lambda c: c.state is ChangeState.AWAITING_APPROVAL),
                what="drift change on ie-sw2")
    change = (await drift_change())[0]
    say("ie-sw2 drifted from intent. Intent drone proposed:")
    for ln in change.config.splitlines():
        print(f"           {ln}")
    say(f"Guardian verified it; rollback plan has {len(change.rollback)} lines. "
        "Change is waiting for a human.")

    # ------------------------------------------------------------------ act 2
    act("ACT 2  Chaos: ring link ie-sw2 Gi1/0/1 <-> ie-sw3 Gi1/0/2 fails, ie-sw4 Gi1/0/5 flaps")
    log_before = len(await lab.board.read_stream("signal_log", Signal))
    await lab.network.break_link("ie-sw2", "Gi1/0/1")
    await lab.network.flap("ie-sw4", "Gi1/0/5", 20)

    async def have_findings():
        incs = [i for i in await q.incidents() if i.lane == "fault"]
        keys = {i.key for i in incs if i.findings}
        return {"fault:rep-segment:1", "fault:ptp-domain:0", "fault:device:ie-sw4"} <= keys
    await until(have_findings, what="findings on all three incidents")

    # ------------------------------------------------------------------ act 3
    act("ACT 3  Order: signals into incidents, incidents to the right drones")
    signals = len(await lab.board.read_stream("signal_log", Signal)) - log_before
    incs = [i for i in await q.incidents() if i.lane == "fault"]
    say(f"{signals} signals in -> {len(incs)} incidents")
    by_id = {d.id: d for d in await q.directives()}
    for inc in incs:
        tag = " STORM" if inc.storm else ""
        say(f"incident {inc.key}{tag}: {inc.signal_count} signals "
            f"({inc.suppressed} suppressed), devices {', '.join(inc.devices)}")
        for skill, did in inc.skills.items():
            d = by_id.get(did)
            if d:
                print(f"           {skill:<18} -> {d.assigned_to or '-'}  [{d.state.value}]")
        if inc.findings:
            f = inc.findings[-1]
            print(f"           finding ({f['confidence']}): {f['summary']}")
            print(f"           advice: {f['recommendation']}")

    # ------------------------------------------------------------------ act 4
    act("ACT 4  Consent: identities, roles, and a change that waits for the ring")
    for who, token in (("intern", lab.tokens["intern"]), ("forged", "manish.deadbeef")):
        try:
            await q.vote(change.id, token, "approve", change.content_hash)
        except ConsentError as exc:
            say(f"{who} tried to approve: refused ({exc})")
    await q.vote(change.id, lab.tokens["manish"], "approve", change.content_hash,
                 "ntp fix, low risk")
    say("manish approved.")
    await until(lambda: _any(drift_change, lambda c: "open incident" in c.reason),
                what="incident freeze")
    say(f"change {change.id}: {(await drift_change())[0].reason}")

    say("a technician replaces the optic; the ring closes")
    await lab.network.restore_link("ie-sw2", "Gi1/0/1")
    await until(lambda: _any(drift_change, lambda c: c.state is ChangeState.APPLIED),
                timeout=20, what="drift change applied")
    say("REP incident closed, change ran: pre-check, apply under rollback timer, soak, "
        "post-check, confirm")
    await until(lambda: _converged(lab, "ie-sw2"), what="ie-sw2 converged")
    say("ie-sw2 now matches intent.")

    # ------------------------------------------------------------------ act 5
    act("ACT 5  GitOps: 'add camera VLAN 30 on every ring-1 switch'")
    patch = {dev: {"vlan 30|name": "cameras",
                   "interface Gi1/0/6|description": "cell-camera-2",
                   "interface Gi1/0/6|switchport access vlan": "30"}
             for dev in sorted(lab.network.devices)}
    await q.request(lab.tokens["manish"], "add camera VLAN 30 on every ring-1 switch",
                    intent_patch=patch)
    await until(lambda: _true(bool(lab.repo.pull_requests())), what="pull request")
    pr = lab.repo.pull_requests()[0]
    say(f"pull request {pr.url} opened by {pr.author} for {', '.join(sorted(pr.patch))}")
    try:
        lab.repo.approve(pr.id, "manish")
    except PermissionError as exc:
        say(f"manish tried to approve his own PR: {exc}")
    lab.repo.approve(pr.id, "deepak")
    sha = lab.repo.merge(pr.id)
    say(f"deepak approved, PR merged -> intent {lab.repo.source(sha)}")

    async def rollout():
        return [c for c in await q.changes() if c.rollout == lab.repo.source(sha)]

    await until(lambda: _count(rollout, lambda c: c.state is ChangeState.APPLIED, 1),
                timeout=20, what="canary")
    canary = next(c for c in await rollout() if c.state is ChangeState.APPLIED)
    say(f"canary {canary.device} applied; PR approval counted for "
        f"{', '.join(v.principal for v in canary.votes)}")

    await q.freeze(lab.tokens["deepak"], "plant shift change, hold all changes")
    say("deepak hit the freeze switch")
    await asyncio.sleep(0.8)
    waiting = [c for c in await rollout() if c.state is ChangeState.APPROVED]
    for c in waiting[:1]:
        say(f"  {c.device}: {c.reason}")
    await q.unfreeze(lab.tokens["deepak"])
    say("freeze lifted")

    await until(lambda: _count(rollout, lambda c: c.state is ChangeState.APPLIED, 2),
                timeout=20, what="second change")
    old = lab.leader
    await old.stop(release=False)
    say(f"{old.name} crashed mid-rollout (lease left to expire)")
    await until(lambda: _true(lab.leader is not None and lab.leader is not old),
                what="new Queen")
    say(f"{lab.leader.name} ascended and carries on from the blackboard")

    await until(lambda: _count(rollout, lambda c: c.state is ChangeState.APPLIED, 4),
                timeout=30, what="rollout complete")
    say("rollout order (one ring member at a time, canary first):")
    for c in sorted(await rollout(), key=lambda c: c.applied_at):
        print(f"           {c.applied_at:%H:%M:%S.%f}"[:-3] + f"  {c.device}")

    # ------------------------------------------------------------------ act 6
    act("ACT 6  Report")
    tshoot_gw = lab.policy.for_drone("Four of Nine, Tshoot")
    for attempt, call in (("write config", tshoot_gw.apply("ie-sw1", ["reload"], 5)),
                          ("pipe a show", tshoot_gw.run_show("ie-sw1", "show run | inc pass"))):
        try:
            await call
        except PermissionError as exc:
            say(f"gateway refused a tshoot drone trying to {attempt}: {exc}")
    gs = gateway_summary(lab.policy)
    audit = await lab.board.read_stream(STREAM, AuditRecord)
    changes = await q.changes()
    say(f"gateway: {gs['calls']} calls, {gs['writes']} writes (all by the Deployer), "
        f"{gs['denied']} denied")
    say(f"changes: {sum(c.state is ChangeState.APPLIED for c in changes)} applied, "
        f"{len(changes)} total; audit trail: {len(audit)} records")
    for d in lab.drones.values():
        extra = f", reported {d.reports} deviations" if hasattr(d, "reports") else ""
        print(f"           {d.designation:<34} handled {d.handled}{extra}")
    await lab.stop()
    print("\nResistance is futile.")


async def _any(fetch, pred) -> bool:
    return any(pred(x) for x in await fetch())


async def _count(fetch, pred, n: int) -> bool:
    return sum(1 for x in await fetch() if pred(x)) >= n


async def _true(value: bool) -> bool:
    return value


async def _converged(lab, device: str) -> bool:
    report = await lab.leader.drift.report(device) if lab.leader else None
    return report is not None and report.empty


if __name__ == "__main__":
    asyncio.run(main())
