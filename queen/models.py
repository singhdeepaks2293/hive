"""queen/models.py -- domain models for the Queen.

These live here until blackboard/ lands; then Event -> blackboard/events.py,
Task/TaskSpec -> blackboard/tasks.py, and queen imports them from there.
Pydantic v1/v2 compatible (only plain BaseModel/Field features are used).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class EventKind(str, Enum):
    SYSLOG = "syslog"
    SNMP = "snmp"
    REQUEST = "request"        # human chat
    APPROVAL = "approval"      # human / guardian decision
    AUDIT = "audit"            # queen-generated audit trail record


class Event(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: EventKind
    source: str = "unknown"                 # device name or user id
    ts: datetime = Field(default_factory=now)
    severity: int = 6                       # syslog scale: 0 emerg .. 7 debug
    mnemonic: Optional[str] = None          # e.g. "LINK-3-UPDOWN"
    protocol: Optional[str] = None          # "rep" | "ptp" | "static" | "bgp" ...
    device: Optional[str] = None
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        # severity + state included: a link DOWN (sev 3) and its UP recovery (sev 5)
        # are different facts and must never collapse into one dedup bucket
        parts = [self.kind.value, self.device or self.source, self.mnemonic or "",
                 str(self.severity), str(self.payload.get("trap", "")),
                 str(self.payload.get("interface", "")), str(self.payload.get("state", ""))]
        return "|".join(parts)


class Risk(str, Enum):
    READ_ONLY = "read_only"
    CHANGE = "change"


class TaskState(str, Enum):
    PENDING = "pending"                      # dependencies not yet satisfied
    AWAITING_APPROVAL = "awaiting_approval"  # quorum gate
    READY = "ready"                          # claimable by a worker
    LEASED = "leased"                        # a worker holds the lease
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"                    # quorum vetoed
    CANCELLED = "cancelled"                  # dependency died / operator cancel


ACTIVE_STATES = {TaskState.PENDING, TaskState.AWAITING_APPROVAL, TaskState.READY, TaskState.LEASED}
TERMINAL_STATES = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.REJECTED, TaskState.CANCELLED}


class TaskSpec(BaseModel):
    """What the Router hands the Queen. `depends_on` indices are local to the batch."""
    type: str                                # collect | diagnose | backup | verify_change ...
    owner: str                               # scout | parser | analyst | expert:static ...
    device: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = 2                        # 0 critical .. 3 low
    risk: Risk = Risk.READ_ONLY
    depends_on: list[int] = Field(default_factory=list)
    skill_refs: list[str] = Field(default_factory=list)   # skills_out/ folder names


class Approval(BaseModel):
    approver: str                            # "human:deepak" | "guardian"
    decision: Literal["approve", "reject"]
    note: str = ""
    ts: datetime = Field(default_factory=now)

    @property
    def role(self) -> str:
        return self.approver.split(":", 1)[0]


class QuorumRule(BaseModel):
    """e.g. required={"guardian": 1, "human": 1} -> one of each must approve."""
    required: dict[str, int] = Field(default_factory=dict)
    veto: bool = True                        # any single reject kills the task


class Task(BaseModel):
    id: str = Field(default_factory=new_id)
    spec: TaskSpec
    origin_event: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)   # real task ids
    state: TaskState = TaskState.PENDING
    approvals: list[Approval] = Field(default_factory=list)
    attempts: int = 0
    not_before: Optional[datetime] = None    # retry backoff gate
    lease_holder: Optional[str] = None
    lease_expires: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    def dedup_key(self) -> str:
        """Idempotency: one active task per (type, owner, device, params)."""
        s = self.spec
        return "|".join([s.type, s.owner, s.device or "",
                         json.dumps(s.params, sort_keys=True, default=str)])


class QueenConfig(BaseModel):
    tick_s: float = 0.2                      # scheduler pass interval
    lease_ttl_s: float = 120.0               # worker lease duration
    max_attempts: int = 3
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 300.0
    dedup_window_s: float = 30.0             # collapse identical events in this window
    storm_threshold: int = 10                # N identical events -> one critical diagnose
    device_concurrency: int = 2              # max leased tasks per device
    approval_ttl_s: float = 3600.0           # approvals go stale after this
    max_followups: int = 5                   # fan-out cap per completion
    approval_policy: dict[Risk, QuorumRule] = Field(default_factory=lambda: {
        Risk.READ_ONLY: QuorumRule(),                                   # no gate
        Risk.CHANGE: QuorumRule(required={"guardian": 1, "human": 1}),  # verify + human
    })