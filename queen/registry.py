"""queen/registry.py -- the vinculum: who is in the collective and what each can do.

Drones join with a manifest (designation, skills, platforms, capacity).
They stay eligible while they heartbeat. A drone that fails `sever_after`
times in a row is severed for `sever_s`, then regenerates and rejoins.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from .audit import Audit
from .blackboard import Blackboard
from .models import Drone, DroneManifest, DroneStatus, QueenConfig


def skill_score(drone_skills: list[str], required: str) -> int:
    """-1 if no skill covers `required`; otherwise the depth of the best cover.
    'tshoot' covers 'tshoot.rep' with score 1; 'tshoot.rep' covers it with 2."""
    req = required.split(".")
    best = -1
    for skill in drone_skills:
        parts = skill.split(".")
        if parts == req[:len(parts)]:
            best = max(best, len(parts))
    return best


class Registry:
    def __init__(self, board: Blackboard, cfg: QueenConfig,
                 clock: Callable[[], datetime], audit: Audit) -> None:
        self.board = board
        self.cfg = cfg
        self.clock = clock
        self.audit = audit

    async def register(self, manifest: DroneManifest) -> Drone:
        if not manifest.skills:
            raise ValueError(f"{manifest.designation} declares no skills")
        if manifest.capacity < 1:
            raise ValueError("capacity must be >= 1")
        now = self.clock()
        drone = await self.get(manifest.designation)
        if drone is None:
            drone = Drone(manifest=manifest, registered_at=now, last_seen=now)
            await self.audit("drone_joined", manifest.designation,
                             f"skills={manifest.skills}")
        else:
            drone.manifest = manifest       # skills may change; severance survives re-join
            drone.last_seen = now
            await self.audit("drone_rejoined", manifest.designation,
                             f"skills={manifest.skills}")
        await self.board.put("drones", manifest.designation, drone)
        return drone

    async def get(self, designation: str) -> Optional[Drone]:
        return await self.board.get("drones", designation, Drone)

    async def all(self) -> list[Drone]:
        return await self.board.scan("drones", Drone)

    async def touch(self, designation: str) -> Drone:
        drone = await self.get(designation)
        if drone is None:
            raise PermissionError(f"{designation} is not part of the collective")
        drone.last_seen = self.clock()
        await self.board.put("drones", designation, drone)
        return drone

    def alive(self, drone: Drone) -> bool:
        return self.clock() - drone.last_seen <= timedelta(seconds=self.cfg.drone_ttl_s)

    def eligible(self, drones: list[Drone], skill: str,
                 platform: Optional[str]) -> list[tuple[int, Drone]]:
        out = []
        for d in drones:
            if d.status is not DroneStatus.ACTIVE or not self.alive(d):
                continue
            if platform and d.manifest.platforms and platform not in d.manifest.platforms:
                continue
            score = skill_score(d.manifest.skills, skill)
            if score >= 0:
                out.append((score, d))
        return out

    async def record_success(self, designation: str) -> None:
        drone = await self.get(designation)
        if drone is None:
            return
        drone.completed += 1
        drone.consecutive_failures = 0
        await self.board.put("drones", designation, drone)

    async def record_failure(self, designation: str, reason: str) -> None:
        drone = await self.get(designation)
        if drone is None:
            return
        drone.failed += 1
        drone.consecutive_failures += 1
        if (drone.status is DroneStatus.ACTIVE
                and drone.consecutive_failures >= self.cfg.sever_after):
            drone.status = DroneStatus.SEVERED
            drone.severed_until = self.clock() + timedelta(seconds=self.cfg.sever_s)
            await self.audit("drone_severed", designation,
                             f"{drone.consecutive_failures} failures in a row, last: {reason}")
        await self.board.put("drones", designation, drone)

    async def upkeep(self) -> None:
        now = self.clock()
        for d in await self.all():
            if (d.status is DroneStatus.SEVERED and d.severed_until is not None
                    and d.severed_until <= now):
                d.status = DroneStatus.ACTIVE
                d.severed_until = None
                d.consecutive_failures = 0
                await self.board.put("drones", d.designation, d)
                await self.audit("drone_regenerated", d.designation, "rejoined the collective")
