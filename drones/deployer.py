"""drones/deployer.py -- the Deployer unimatrix: the ONLY drone that writes.

    pre-check   device healthy (REP complete, PTP locked, links up). Abort if not.
    snapshot    current config (the gateway also checkpoints)
    apply       under a rollback timer; if this drone dies, the device reverts itself
    soak        wait, keeping the lease alive
    post-check  same health checks, plus every intended setting actually present
    confirm     or roll back, and report failure (changes are never retried)
"""
from __future__ import annotations

import asyncio
from typing import Any

from netskills import normalize
from queen.models import Directive

from . import checks
from .base import Drone, DroneFailure, handles


class DeployerDrone(Drone):
    unimatrix = "deployer"

    def __init__(self, *args: Any, soak_s: float = 30.0, revert_minutes: int = 5,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.soak_s = soak_s
        self.revert_minutes = revert_minutes

    async def _unhealthy(self, device: str) -> list[str]:
        return [c.detail for c in (await checks.device_health(self.gw, device)).values()
                if not c.ok]

    @handles("config.apply")
    async def apply(self, d: Directive) -> dict[str, Any]:
        lines = [ln for ln in str(d.params.get("config", "")).splitlines() if ln.strip()]
        if not lines or not d.device:
            raise DroneFailure("nothing to apply", retryable=False)

        bad = await self._unhealthy(d.device)
        if bad:
            raise DroneFailure(f"pre-check failed, config untouched: {'; '.join(bad)}",
                               retryable=False)
        before = await self.gw.get_config(d.device)
        expected = normalize.apply_lines(before, lines)
        checkpoint = await self.gw.apply(d.device, lines, self.revert_minutes)

        await asyncio.sleep(self.soak_s)
        after = await self.gw.get_config(d.device)
        problems = await self._unhealthy(d.device)
        missing = [k for k in normalize.touched_keys(lines) if after.get(k) != expected.get(k)]
        if missing:
            problems.append(f"settings not as intended: {', '.join(sorted(missing))}")
        if problems:
            await self.gw.rollback(d.device, checkpoint)
            raise DroneFailure(f"post-check failed, rolled back: {'; '.join(problems)}",
                               retryable=False)
        await self.gw.confirm(d.device, checkpoint)
        return {"applied": True, "checkpoint": checkpoint,
                "observed": await self.gw.get_config(d.device)}
