"""drones/guardian.py -- the Guardian unimatrix: verifies a change BEFORE anyone votes.

Checks, in order:
  1. forbidden     commands that destroy state (reload, erase, ...): always refused
  2. mgmt plane    AAA, VTY, SSH, default route, mgmt interface: break-glass only
  3. high risk     touching REP or PTP config: allowed, flagged in the evidence
  4. pre-condition the device's failure domains must be healthy right now
                   (REP segment COMPLETE, PTP LOCKED), unless break-glass
  5. rollback      lines that undo the change, computed from the live config

Result: {"passed", "content_hash" (echoed, binds the verdict to this exact
change), "evidence", "rollback"}.
"""
from __future__ import annotations

import re
from typing import Any

from netskills import normalize
from queen.models import Directive

from . import checks
from .base import Drone, DroneFailure, handles

FORBIDDEN = [r"^\s*reload\b", r"^\s*write erase\b", r"^\s*erase\b", r"^\s*format\b",
             r"^\s*delete\b", r"^\s*crypto key zeroize\b", r"^\s*no ip routing\b"]
MGMT_PLANE = [r"^\s*(no\s+)?aaa\b", r"^\s*(no\s+)?username\b", r"^\s*line vty\b",
              r"^\s*(no\s+)?ip ssh\b", r"^\s*(no\s+)?ip route 0\.0\.0\.0 0\.0\.0\.0\b",
              r"^\s*interface (Vlan1|mgmt\d*|GigabitEthernet0/0)\s*$",
              r"^\s*(no\s+)?snmp-server community\b"]
HIGH_RISK = [r"\brep segment\b", r"\brep preempt\b", r"^\s*(no\s+)?ptp\b"]


class GuardianDrone(Drone):
    unimatrix = "guardian"

    @handles("verify.change")
    async def verify(self, d: Directive) -> dict[str, Any]:
        lines = [ln for ln in str(d.params.get("config", "")).splitlines() if ln.strip()]
        if not lines or not d.device:
            raise DroneFailure("nothing to verify", retryable=False)
        glass = bool(d.params.get("break_glass"))
        problems: list[str] = []
        risks: list[str] = []
        for ln in lines:
            if any(re.search(p, ln) for p in FORBIDDEN):
                problems.append(f"forbidden: {ln.strip()}")
            if any(re.search(p, ln) for p in MGMT_PLANE):
                (risks if glass else problems).append(f"management plane: {ln.strip()}")
            if any(re.search(p, ln) for p in HIGH_RISK):
                risks.append(f"ring/timing config: {ln.strip()}")

        health = await checks.device_health(self.gw, d.device)
        for name in ("rep", "ptp"):
            c = health[name]
            if not c.ok:
                (risks if glass else problems).append(f"pre-condition {name}: {c.detail}")

        current = await self.gw.get_config(d.device)
        rollback = normalize.rollback(current, lines)
        domains = d.params.get("domains") or {}
        return {
            "passed": not problems,
            "content_hash": d.params.get("content_hash"),
            "rollback": rollback,
            "evidence": {"problems": problems, "risks": risks, "lines": len(lines),
                         "blast_radius": {"device": d.device, **domains},
                         "health": {k: c.detail for k, c in health.items()}},
        }
