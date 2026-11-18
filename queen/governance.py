"""queen/governance.py -- when may an APPROVED change actually start?

Approval says "this change is acceptable". Governance says "and now is a safe
moment". Checked in this order; the first rule that says wait wins:

  1. freeze switch          one human command stops every change
  2. change window          outside the window, wait (break-glass skips this)
  3. domains known          never change a device whose failure domains are unmapped
  4. domain locks           one change at a time per device / REP segment / PTP domain
  5. incident freeze        no routine change into a domain with an open fault
                            incident, unless the change belongs to that incident
                            (break-glass skips this)
  6. rollout / canary       first change of a rollout goes alone, then soaks;
                            a failure anywhere halts the rest (handled in queen.py)

Pure functions, no I/O, so the rules are easy to test and to read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .models import (LOCKING_DOMAINS, Change, ChangeState, ChangeWindow, DeviceDomains,
                     Governance, QueenConfig)


def window_open(windows: list[ChangeWindow], at: datetime) -> bool:
    if not windows:
        return True
    for w in windows:
        local = at.astimezone(ZoneInfo(w.tz))
        start, end = time.fromisoformat(w.start), time.fromisoformat(w.end)
        t = local.time()
        if start <= end:
            if local.weekday() in w.days and start <= t <= end:
                return True
        else:                                            # overnight: 22:00-04:00
            if local.weekday() in w.days and t >= start:
                return True
            yesterday = (local.weekday() - 1) % 7
            if yesterday in w.days and t <= end:
                return True
    return False


def lock_keys(domains: DeviceDomains) -> set[str]:
    return {f"device:{domains.device}"} | {
        f"{k}:{v}" for k, v in domains.domains.items() if k in LOCKING_DOMAINS}


@dataclass
class Picture:
    """Everything governance needs, gathered once per Queen tick."""
    governance: Governance
    changes: list[Change]
    domains: dict[str, DeviceDomains]
    open_fault_incidents: dict[str, str] = field(default_factory=dict)  # domain key -> incident id


def may_start(change: Change, pic: Picture, cfg: QueenConfig,
              now: datetime) -> tuple[bool, str]:
    if pic.governance.frozen:
        return False, f"freeze switch on ({pic.governance.by}: {pic.governance.reason})"
    if not change.break_glass and not window_open(cfg.change_windows, now):
        return False, "outside change window"

    mine = pic.domains.get(change.device)
    if mine is None:
        return False, "failure domains unknown; waiting for topology.map"

    my_locks = lock_keys(mine)
    for other in pic.changes:
        if other.id == change.id or other.state is not ChangeState.APPLYING:
            continue
        theirs = pic.domains.get(other.device)
        held = lock_keys(theirs) if theirs else {f"device:{other.device}"}
        shared = sorted(my_locks & held)
        if shared:
            return False, f"{shared[0]} locked by change {other.id} on {other.device}"

    if not change.break_glass:
        for key in sorted(mine.keys()):
            inc = pic.open_fault_incidents.get(key)
            if inc is not None and inc != change.incident_id:
                return False, f"open incident {inc} on {key}"

    if change.rollout:
        siblings = [c for c in pic.changes if c.rollout == change.rollout and c.id != change.id]
        landed = [c for c in siblings if c.state is ChangeState.APPLIED and c.applied_at]
        if not landed:
            if any(c.state is ChangeState.APPLYING for c in siblings):
                return False, "canary in flight"
            return True, "canary"
        first = min(c.applied_at for c in landed)
        soak_end = first + timedelta(seconds=cfg.canary_soak_s)
        if now < soak_end:
            return False, f"canary soaking until {soak_end:%H:%M:%S}"
    return True, "clear"


def rollout_failure(change: Change, changes: list[Change]) -> Optional[Change]:
    """The sibling whose failure halts this change's rollout, if any."""
    if not change.rollout:
        return None
    for c in changes:
        if c.rollout == change.rollout and c.id != change.id and c.state is ChangeState.FAILED:
            return c
    return None
