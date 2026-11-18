"""drones/mapper.py -- the Map unimatrix: which failure domains each device is in.

rep-segment from `show rep topology`, ptp-domain from `show ptp clock`,
site from inventory (NetBox later). The Queen will not let a change start on
a device whose domains are unknown.
"""
from __future__ import annotations

from typing import Any, Optional

from queen.models import Directive

from .base import Drone, handles


class MapDrone(Drone):
    unimatrix = "map"

    def __init__(self, *args: Any, sites: Optional[dict[str, str]] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sites = sites or {}

    @handles("topology.map")
    async def map_device(self, d: Directive) -> dict[str, Any]:
        domains: dict[str, str] = {}
        rep = [r for r in await self.gw.run_show(d.device, "show rep topology")
               if r["bridge"] == d.device]
        ports: dict[str, str] = {}
        if rep:
            domains["rep-segment"] = str(rep[0]["segment"])
            ports = {r["port"]: f"rep-segment:{r['segment']}" for r in rep}
        ptp = await self.gw.run_show(d.device, "show ptp clock")
        if ptp:
            domains["ptp-domain"] = str(ptp[0]["domain"])
        if d.device in self.sites:
            domains["site"] = self.sites[d.device]
        version = await self.gw.run_show(d.device, "show version")
        return {"domains": domains, "ports": ports,
                "platform": version[0]["platform"] if version else None}
