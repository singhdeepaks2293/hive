"""queen/blackboard.py -- storage contract + in-memory dev stand-in.

The Queen only talks to the Blackboard protocol. When blackboard/store.py
(Postgres) lands, implement the same four methods there and pass it in;
nothing in queen/ changes. InMemoryBlackboard is for tests and the demo.
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol

from .models import Event, Task, TaskState


class Blackboard(Protocol):
    async def append(self, event: Event) -> None: ...
    async def save(self, task: Task) -> None: ...
    async def get(self, task_id: str) -> Optional[Task]: ...
    async def find(self, states: Optional[Iterable[TaskState]] = None) -> list[Task]: ...


class InMemoryBlackboard:
    """Not crash-safe, single process. Mirrors the future Postgres store API."""

    def __init__(self) -> None:
        self.events: list[Event] = []        # append-only
        self.tasks: dict[str, Task] = {}

    async def append(self, event: Event) -> None:
        self.events.append(event)

    async def save(self, task: Task) -> None:
        self.tasks[task.id] = task

    async def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    async def find(self, states: Optional[Iterable[TaskState]] = None) -> list[Task]:
        wanted = set(states) if states is not None else None
        return [t for t in self.tasks.values() if wanted is None or t.state in wanted]