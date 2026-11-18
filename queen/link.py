"""queen/link.py -- how drones and humans reach whichever Queen is leading.

A QueenLink holds every Queen instance it knows and forwards each call to the
current leader. If that Queen is standing by (NotLeader), the next one is
tried. When the leader dies, its lease expires, a standby ascends, and calls
start landing there. Callers never need to know which Queen they talk to.

In-process today. Over the network the same pattern becomes a client with a
list of Queen endpoints.
"""
from __future__ import annotations

from typing import Any

from .queen import NotLeader, Queen

_FORWARDED = {"register", "heartbeat", "receive", "renew", "complete", "fail",
              "publish_desired", "report", "request", "vote", "freeze", "unfreeze",
              "ingest", "incident", "changes", "incidents", "directives", "governance"}


class QueenLink:
    def __init__(self, *queens: Queen) -> None:
        if not queens:
            raise ValueError("a link needs at least one Queen")
        self.queens = list(queens)

    def __getattr__(self, name: str) -> Any:
        if name not in _FORWARDED:
            raise AttributeError(name)

        async def call(*args: Any, **kwargs: Any) -> Any:
            ordered = sorted(self.queens, key=lambda q: not q.leading)   # last known leader first
            last: Exception = NotLeader("no Queen is leading")
            for q in ordered:
                try:
                    return await getattr(q, name)(*args, **kwargs)
                except NotLeader as exc:
                    last = exc
            raise last
        return call
