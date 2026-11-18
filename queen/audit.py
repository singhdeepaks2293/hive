"""queen/audit.py -- append-only record of every decision the Queen makes."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from .blackboard import Blackboard
from .models import AuditRecord

log = logging.getLogger("hive.queen")

STREAM = "audit"


class Audit:
    def __init__(self, board: Blackboard, clock: Callable[[], datetime], actor: str) -> None:
        self.board = board
        self.clock = clock
        self.actor = actor

    async def __call__(self, action: str, subject: Optional[str] = None,
                       detail: str = "") -> None:
        rec = AuditRecord(ts=self.clock(), actor=self.actor, action=action,
                          subject=subject, detail=detail)
        await self.board.append(STREAM, rec)
        log.info("[%s] %s %s %s", self.actor, action, subject or "-", detail)
