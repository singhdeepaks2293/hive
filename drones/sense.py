"""drones/sense.py -- the Sense unimatrix: eyes of the collective. Read-only."""
from __future__ import annotations

import hashlib
from typing import Any

from queen.models import Directive

from .base import Drone, DroneFailure, handles


class SenseDrone(Drone):
    unimatrix = "sense"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.backups: dict[str, list[dict[str, Any]]] = {}

    @handles("collect.show")
    async def collect_show(self, d: Directive) -> dict[str, Any]:
        commands = d.params.get("commands") or []
        if not commands or not d.device:
            raise DroneFailure("collect.show needs a device and commands", retryable=False)
        evidence = {}
        for cmd in commands:
            try:
                evidence[cmd] = await self.gw.run_show(d.device, cmd)
            except PermissionError as exc:
                evidence[cmd] = {"error": str(exc)}      # keep going; report what was refused
        return {"evidence": evidence}

    @handles("collect.config")
    async def collect_config(self, d: Directive) -> dict[str, Any]:
        return {"observed": await self.gw.get_config(d.device)}

    @handles("backup.run")
    async def backup(self, d: Directive) -> dict[str, Any]:
        text = await self.gw.get_running_text(d.device)
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        self.backups.setdefault(d.device, []).append({"sha": digest, "text": text})
        return {"backup": {"device": d.device, "sha": digest, "lines": len(text.splitlines())}}

    @handles("inventory.refresh")
    async def inventory(self, d: Directive) -> dict[str, Any]:
        rows = await self.gw.run_show(d.device, "show version")
        return {"facts": rows[0] if rows else {}}
