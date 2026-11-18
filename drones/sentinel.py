"""drones/sentinel.py -- the Sentinel unimatrix: keeps watch between events.

Patrols every device, compares against a learned baseline, and reports
deviations to the Queen as HEALTH signals. It never fixes anything; the
Queen routes what it reports to the right tshoot drone.

Baselines live in the drone for now. Moving them to the blackboard lets any
Sentinel take over a patrol without relearning.
"""
from __future__ import annotations

from typing import Any

from queen.models import Directive, Signal, SignalKind

from . import checks
from .base import Drone, handles

CRC_DELTA_ALERT = 100


class SentinelDrone(Drone):
    unimatrix = "sentinel"

    def __init__(self, *args: Any, devices: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.devices = devices
        self.crc_baseline: dict[tuple[str, str], int] = {}
        self.reports = 0

    async def patrol(self) -> None:
        for device in self.devices:
            await self._inspect(device)

    async def _inspect(self, device: str) -> list[Signal]:
        raised = []
        health = await checks.device_health(self.gw, device)
        rep, ptp = health["rep"], health["ptp"]
        if not rep.ok:
            raised.append(Signal(kind=SignalKind.HEALTH, device=device, protocol="rep",
                                 severity=4, text=rep.detail,
                                 payload={"check": "rep.segment_complete",
                                          "segment": rep.data.get("segment")}))
        if not ptp.ok:
            raised.append(Signal(kind=SignalKind.HEALTH, device=device, protocol="ptp",
                                 severity=4, text=ptp.detail,
                                 payload={"check": "ptp.locked",
                                          "ptp_domain": ptp.data.get("domain")}))
        for row in health["interfaces"].data["rows"]:
            key = (device, row["interface"])
            base = self.crc_baseline.setdefault(key, row["crc"])
            if row["crc"] - base >= CRC_DELTA_ALERT:
                raised.append(Signal(kind=SignalKind.HEALTH, device=device,
                                     protocol="interface", severity=4,
                                     text=f"{row['interface']} CRC +{row['crc'] - base}",
                                     payload={"check": "interface.crc",
                                              "interface": row["interface"]}))
                self.crc_baseline[key] = row["crc"]
        for sig in raised:
            await self.link.report(self.designation, sig)
            self.reports += 1
        return raised

    @handles("health.check")
    async def check_on_demand(self, d: Directive) -> dict[str, Any]:
        health = await checks.device_health(self.gw, d.device)
        return {"health": {k: {"ok": c.ok, "detail": c.detail} for k, c in health.items()}}
