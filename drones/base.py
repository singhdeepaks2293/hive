"""drones/base.py -- what every drone shares.

A drone:
  - joins the collective with a manifest of skills built from its @handles methods
  - receives ONE directive at a time from the Queen (never picks its own work)
  - renews the lease while working, and stops the moment the lease is lost
  - returns a result dict, or raises DroneFailure(retryable=...)

Results may carry: facts / evidence, "observed" (collectors), "domains" (map),
"findings" (tshoot), "proposal" (intent drones only), "passed" (guardian).
The Queen decides what any of it means.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from queen.models import Directive, DroneManifest
from queen.queen import NotLeader
from queen.registry import skill_score

log = logging.getLogger("hive.drone")

Handler = Callable[[Directive], Awaitable[dict[str, Any]]]


class DroneFailure(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def handles(*skills: str) -> Callable[[Handler], Handler]:
    def mark(fn: Handler) -> Handler:
        fn._skills = skills                              # type: ignore[attr-defined]
        return fn
    return mark


class Drone:
    unimatrix = "drone"

    def __init__(self, link: Any, designation: str, gateway: Any = None, *,
                 skills: Optional[list[str]] = None, capacity: int = 1,
                 platforms: Optional[list[str]] = None, renew_every_s: float = 20.0) -> None:
        self.link = link                                 # Queen or QueenLink
        self.designation = designation
        self.gw = gateway
        self.capacity = capacity
        self.platforms = platforms or []
        self.renew_every_s = renew_every_s
        self.handled = 0
        self._handlers: dict[str, Handler] = {}
        for name in dir(type(self)):
            if isinstance(getattr(type(self), name, None), property):
                continue
            fn = getattr(self, name)
            for skill in getattr(fn, "_skills", ()):
                if skills is None or skill in skills:
                    self._handlers[skill] = fn
        if not self._handlers:
            raise ValueError(f"{designation} would join with no skills")

    @property
    def manifest(self) -> DroneManifest:
        return DroneManifest(designation=self.designation, skills=sorted(self._handlers),
                             platforms=self.platforms, capacity=self.capacity)

    async def join(self) -> None:
        await self.link.register(self.manifest)

    def _handler_for(self, skill: str) -> Handler:
        best = max(self._handlers, key=lambda s: skill_score([s], skill))
        if skill_score([best], skill) < 0:
            raise DroneFailure(f"{self.designation} has no handler for {skill}", retryable=False)
        return self._handlers[best]

    async def step(self) -> bool:
        """Receive and finish at most one directive. True if one was handled."""
        d = await self.link.receive(self.designation)
        if d is None:
            return False
        work = asyncio.create_task(self._run(d))
        renewer = asyncio.create_task(self._keep_lease(d, work))
        try:
            outcome = await work
        except asyncio.CancelledError:
            log.warning("%s lost the lease on %s and stopped", self.designation, d.id)
            return True
        finally:
            renewer.cancel()
        kind, payload = outcome
        self.handled += 1
        if kind == "ok":
            await self._report(d, lambda: self.link.complete(d.id, self.designation, payload))
        else:
            await self._report(d, lambda: self.link.fail(d.id, self.designation, payload["error"],
                                                         retryable=payload["retryable"]))
        return True

    async def _report(self, d: Directive, send: Callable[[], Awaitable[Any]]) -> None:
        """Keep delivering the outcome through a Queen failover. Work that was done
        must not be lost because the Queen changed mid-flight; the lease on the
        blackboard says whether the result still counts."""
        deadline = asyncio.get_running_loop().time() + max(self._ttl(d), 1.0)
        while True:
            try:
                await send()
                return
            except NotLeader:
                if asyncio.get_running_loop().time() >= deadline:
                    log.error("%s could not report %s before its lease ran out",
                              self.designation, d.id)
                    return
                await asyncio.sleep(0.1)
            except PermissionError as exc:               # lease lost: the result is void
                log.error("%s result for %s refused: %s", self.designation, d.id, exc)
                return

    @staticmethod
    def _ttl(d: Directive) -> float:
        if d.lease_expires is None:
            return 0.0
        return (d.lease_expires - datetime.now(timezone.utc)).total_seconds()

    async def _run(self, d: Directive) -> tuple[str, dict[str, Any]]:
        try:
            return "ok", await self._handler_for(d.skill)(d)
        except DroneFailure as exc:
            return "fail", {"error": str(exc), "retryable": exc.retryable}
        except Exception as exc:                         # bugs and device errors: retryable
            log.exception("%s crashed on %s", self.designation, d.id)
            return "fail", {"error": f"{type(exc).__name__}: {exc}", "retryable": True}

    async def _keep_lease(self, d: Directive, work: asyncio.Task) -> None:
        # renew at a third of the remaining lease, never slower than renew_every_s
        every = min(self.renew_every_s, max(self._ttl(d) / 3, 0.1))
        while not work.done():
            await asyncio.sleep(every)
            try:
                ok = await self.link.renew(d.id, self.designation)
            except NotLeader:
                continue                                 # Queen changing; the lease is on the board
            if not ok:
                work.cancel()                            # fenced off: stop touching anything
                return

    async def patrol(self) -> None:
        """Override for drones with their own rhythm (Sentinel, Intent)."""

    async def run(self, stop: asyncio.Event, idle_s: float = 0.05,
                  patrol_every_s: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        joined = False
        next_patrol = 0.0
        while not stop.is_set():
            try:
                if not joined:
                    await self.join()
                    joined = True
                if loop.time() >= next_patrol:
                    next_patrol = loop.time() + patrol_every_s
                    await self.patrol()
                if not await self.step():
                    await asyncio.sleep(idle_s)
            except NotLeader:
                await asyncio.sleep(0.2)                 # the Queen is changing; wait for the next
            except Exception:
                log.exception("%s loop error", self.designation)
                await asyncio.sleep(0.5)
