"""queen/blackboard.py -- the collective's shared memory.

The Queen keeps no state of her own. Everything (inbox, incidents, directives,
drones, desired/observed state, changes, audit) lives behind this protocol, so
any Queen instance holding the leader lease can pick up where the last one
stopped.

InMemoryBlackboard stores serialized JSON and re-parses on every read. That
gives the same copy semantics as a real database: mutate a model, then `put`
it, or the change is lost. Tests catch any missing `put` because of this.

Postgres mapping (blackboard/store.py later):
    collections -> table kv(collection text, key text, value jsonb, primary key(collection, key))
    streams     -> table stream(stream text, id bigserial, value jsonb)
    leases      -> UPDATE ... WHERE holder = $me OR expires < now() RETURNING
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Protocol, TypeVar

from pydantic import BaseModel

from .models import LeaderLease

M = TypeVar("M", bound=BaseModel)


class Blackboard(Protocol):
    async def put(self, collection: str, key: str, value: BaseModel) -> None: ...
    async def get(self, collection: str, key: str, model: type[M]) -> Optional[M]: ...
    async def scan(self, collection: str, model: type[M]) -> list[M]: ...
    async def delete(self, collection: str, key: str) -> None: ...
    async def append(self, stream: str, value: BaseModel) -> None: ...
    async def read_stream(self, stream: str, model: type[M]) -> list[M]: ...
    async def acquire_lease(self, name: str, holder: str, ttl_s: float,
                            at: datetime) -> bool: ...
    async def release_lease(self, name: str, holder: str) -> None: ...


class InMemoryBlackboard:
    """Single process, not crash-safe. Same contract as the Postgres store."""

    def __init__(self) -> None:
        self._kv: dict[str, dict[str, str]] = {}
        self._streams: dict[str, list[str]] = {}
        self._leases: dict[str, LeaderLease] = {}

    async def put(self, collection: str, key: str, value: BaseModel) -> None:
        self._kv.setdefault(collection, {})[key] = value.model_dump_json()

    async def get(self, collection: str, key: str, model: type[M]) -> Optional[M]:
        raw = self._kv.get(collection, {}).get(key)
        return model.model_validate_json(raw) if raw is not None else None

    async def scan(self, collection: str, model: type[M]) -> list[M]:
        return [model.model_validate_json(r) for r in self._kv.get(collection, {}).values()]

    async def delete(self, collection: str, key: str) -> None:
        self._kv.get(collection, {}).pop(key, None)

    async def append(self, stream: str, value: BaseModel) -> None:
        self._streams.setdefault(stream, []).append(value.model_dump_json())

    async def read_stream(self, stream: str, model: type[M]) -> list[M]:
        return [model.model_validate_json(r) for r in self._streams.get(stream, [])]

    async def acquire_lease(self, name: str, holder: str, ttl_s: float,
                            at: datetime) -> bool:
        lease = self._leases.get(name)
        if lease is None or lease.holder == holder or lease.expires <= at:
            self._leases[name] = LeaderLease(name=name, holder=holder,
                                             expires=at + timedelta(seconds=ttl_s))
            return True
        return False

    async def release_lease(self, name: str, holder: str) -> None:
        lease = self._leases.get(name)
        if lease is not None and lease.holder == holder:
            del self._leases[name]
