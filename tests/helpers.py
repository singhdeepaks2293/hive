"""Shared test helpers: a fake clock and a Queen you drive by hand."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from queen import (ConsentPolicy, DroneManifest, HmacAuthenticator, InMemoryBlackboard, Queen,
                   QueenConfig)
from queen.models import DeviceDomains

USERS = {"manish": {"netops-approver"}, "deepak": {"netops-approver"},
         "intern": {"netops-viewer"}}


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def run(coro):
    return asyncio.run(coro)


def make_queen(board=None, clock=None, name="queen", approvals=None, **cfg: Any):
    board = board or InMemoryBlackboard()
    clock = clock or FakeClock()
    auth = HmacAuthenticator(b"test-secret-0123456789", USERS)
    q = Queen(board, authenticator=auth, name=name, clock=clock,
              config=QueenConfig(**cfg), consent_policy=ConsentPolicy(), approvals=approvals)
    return q, board, clock, auth


async def join(q: Queen, designation: str, *skills: str, capacity: int = 1) -> None:
    await q.register(DroneManifest(designation=designation, skills=list(skills),
                                   capacity=capacity))


async def map_device(q: Queen, device: str, **domains: str) -> None:
    await q.board.put("domains", device, DeviceDomains(
        device=device, domains=domains, mapped_by="test", mapped_at=q.clock()))


async def work(q: Queen, designation: str, result: Optional[dict] = None):
    """Tick, receive one directive as `designation`, complete it."""
    await q.tick()
    d = await q.receive(designation)
    if d is not None:
        await q.complete(d.id, designation, result if result is not None else {})
    return d
