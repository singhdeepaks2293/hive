"""drones/tshoot.py -- the Tshoot unimatrix. Read-only, evidence first.

Every handler returns findings:
    {"summary", "confidence" (0..1), "evidence" {command: rows}, "recommendation"}
A recommendation is advice for humans or for config.propose. Tshoot drones
never change anything and never propose changes themselves.

One class carries every specialty; choose which a drone advertises with
`skills=[...]`, e.g. a REP/PTP expert drone vs a generalist.
The REP and PTP reasoning follows skills_out/cisco-ie-rep-configuration and
skills_out/cisco-ie-ptp-over-rep.
"""
from __future__ import annotations

from typing import Any

from queen.models import Directive

from . import checks
from .base import Drone, handles


def finding(summary: str, confidence: float, evidence: dict[str, Any],
            recommendation: str) -> dict[str, Any]:
    return {"findings": [{"summary": summary, "confidence": round(confidence, 2),
                          "evidence": evidence, "recommendation": recommendation}]}


class TshootDrone(Drone):
    unimatrix = "tshoot"

    @handles("tshoot")
    async def generic(self, d: Directive) -> dict[str, Any]:
        """Generalist: gather a health picture and say what stands out."""
        if not d.device:
            return finding("no device given; nothing to inspect", 0.1, {},
                           "ask with a device name")
        health = await checks.device_health(self.gw, d.device)
        bad = [c for c in health.values() if not c.ok]
        focus = d.params.get("focus", "")
        evidence = {c.name: c.detail for c in health.values()}
        if focus == "event_storm":
            return finding(f"event storm on {d.params.get('incident', d.device)}; "
                           f"{len(bad)} unhealthy check(s)", 0.4, evidence,
                           "find the flapping source first; consider flap dampening "
                           "(errdisable flap detection) on the port while it is investigated")
        if bad:
            return finding("; ".join(c.detail for c in bad), 0.5, evidence,
                           "route to a specialist for " + ", ".join(c.name for c in bad))
        return finding("device looks healthy now; likely transient", 0.3, evidence,
                       "watch for recurrence")

    @handles("tshoot.rep")
    async def rep(self, d: Directive) -> dict[str, Any]:
        rep = await checks.rep_check(self.gw, d.device)
        rows = await self.gw.run_show(d.device, "show rep topology detail")
        evidence = {"show rep topology detail": rows}
        if rep.ok:
            return finding(f"REP {rep.detail} now; the failure cleared", 0.5, evidence,
                           "check the logs of the reporting ports for flaps")
        failed = rep.data["failed"]
        links = []
        for r in failed:
            ifs = await self.gw.run_show(r["bridge"], f"show interfaces {r['port']}")
            evidence[f"{r['bridge']} show interfaces {r['port']}"] = ifs
            if ifs and ifs[0]["link_status"] == "down":
                links.append(f"{r['bridge']} {r['port']}")
        seg = rep.data["segment"]
        if links:
            return finding(
                f"REP segment {seg} is open: physical link down between {' and '.join(links)}; "
                "the ring is running on its alternate path with no redundancy left",
                0.85, evidence,
                f"check optics and cabling on {' / '.join(links)}. Do NOT change any other "
                f"member of segment {seg} until it is COMPLETE again: a second failure "
                "partitions the ring")
        return finding(f"REP segment {seg} is open but the links show up; "
                       "suspect REP adjacency (LSL timeout, mismatched segment ID)", 0.55,
                       evidence, "compare `rep segment` config on both ends of the failed ports")

    @handles("tshoot.ptp")
    async def ptp(self, d: Directive) -> dict[str, Any]:
        ptp = await checks.ptp_check(self.gw, d.device)
        rep = await checks.rep_check(self.gw, d.device)
        parent = await self.gw.run_show(d.device, "show ptp parent")
        evidence = {"show ptp clock": ptp.data, "rep": rep.detail, "show ptp parent": parent}
        if ptp.ok:
            return finding(f"PTP {ptp.detail}; it has recovered", 0.5, evidence,
                           "nothing to do; watch offset for a few minutes")
        if not rep.ok:
            return finding(
                f"PTP {ptp.detail} because REP {rep.detail}: the timing path moved when the "
                "ring reconverged, and this clock lost its master",
                0.8, evidence,
                "fix the ring first. PTP should relock on its own once the segment is "
                "COMPLETE; if offset stays high after that, check the boundary clock on the "
                "new path")
        return finding(f"PTP {ptp.detail} with the ring healthy", 0.5, evidence,
                       "check the grandmaster and the PTP config of the ports facing it")

    @handles("tshoot.interface")
    async def interface(self, d: Directive) -> dict[str, Any]:
        iface = d.params.get("interface") or ""
        rows = await self.gw.run_show(d.device, f"show interfaces {iface}".strip())
        evidence = {f"show interfaces {iface}".strip(): rows}
        if not rows:
            return finding(f"{iface or 'interfaces'} not found on {d.device}", 0.3, evidence,
                           "check the interface name")
        r = rows[0]
        if r["link_status"] != "up":
            return finding(f"{r['interface']} is down at layer 1", 0.75, evidence,
                           "check the far end, optic and cable; check errdisable status")
        if r["crc"] > 0:
            return finding(f"{r['interface']} is up but has {r['crc']} CRC errors, "
                           "consistent with flapping on a bad optic or cable", 0.7, evidence,
                           "replace or reseat the optic/cable; check duplex and speed")
        return finding(f"{r['interface']} is up and clean now", 0.4, evidence,
                       "transient; watch for recurrence")

    @handles("tshoot.routing")
    async def routing(self, d: Directive) -> dict[str, Any]:
        return finding("routing specialist not trained yet", 0.1, {},
                       "escalate to a human; add a routing expert drone")
