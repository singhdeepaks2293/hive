"""drones/checks.py -- device health from parsed show output.

Shared by Sentinel (watch), Guardian (pre-condition before approval) and
Deployer (pre-check and post-check around a change). One definition of
"healthy" means the three never disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PTP_OFFSET_LIMIT_NS = 1000


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    data: dict[str, Any]


async def rep_check(gw: Any, device: str) -> Check:
    rows = await gw.run_show(device, "show rep topology")
    if not rows:
        return Check("rep", True, "not a REP member", {})
    failed = [r for r in rows if r["role"] == "Fail"]
    seg = rows[0]["segment"]
    if failed:
        where = ", ".join(f"{r['bridge']} {r['port']}" for r in failed)
        return Check("rep", False, f"segment {seg} open: {where} failed",
                     {"segment": seg, "failed": failed})
    return Check("rep", True, f"segment {seg} complete", {"segment": seg})


async def ptp_check(gw: Any, device: str) -> Check:
    rows = await gw.run_show(device, "show ptp clock")
    if not rows:
        return Check("ptp", True, "PTP not running", {})
    r = rows[0]
    ok = r["state"] == "LOCKED" and abs(int(r["offset_ns"])) <= PTP_OFFSET_LIMIT_NS
    return Check("ptp", ok, f"{r['state']} offset {r['offset_ns']}ns (domain {r['domain']})",
                 dict(r))


async def interface_check(gw: Any, device: str, ports: list[str] | None = None) -> Check:
    rows = await gw.run_show(device, "show interfaces")
    if ports:
        rows = [r for r in rows if r["interface"] in ports]
    down = [r["interface"] for r in rows if r["link_status"] != "up"]
    return Check("interfaces", not down,
                 f"down: {', '.join(down)}" if down else f"{len(rows)} up",
                 {"down": down, "rows": rows})


async def device_health(gw: Any, device: str) -> dict[str, Check]:
    checks = [await rep_check(gw, device), await ptp_check(gw, device),
              await interface_check(gw, device)]
    return {c.name: c for c in checks}
