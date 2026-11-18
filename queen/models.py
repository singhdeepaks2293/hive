"""queen/models.py -- everything the Queen keeps on the blackboard.

Plain pydantic v2 data. Each model maps to one JSONB row in the future
Postgres store, keyed by `id` (or `designation` / `device`). Nothing here
does I/O; the logic lives in order / classify / registry / dispatch /
drift / consent, and queen.py ties it together.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# --------------------------------------------------------------------------- signals
class SignalKind(str, Enum):
    SYSLOG = "syslog"
    TRAP = "trap"
    HEALTH = "health"          # raised by a Sentinel drone: deviation from baseline
    REQUEST = "request"        # authenticated human chat
    DRIFT = "drift"            # raised by the Queen's own drift watch


class Signal(BaseModel):
    """One shape for every input. This is the chaos before the Queen orders it."""
    id: str = Field(default_factory=new_id)
    kind: SignalKind
    source: str = "unknown"                  # device, user, or "queen"
    device: Optional[str] = None
    severity: int = 6                        # syslog scale: 0 emerg .. 7 debug
    mnemonic: Optional[str] = None           # e.g. "REP-4-LINKSTATUS"
    protocol: Optional[str] = None           # "rep" | "ptp" | "bgp" | ...
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    principal: Optional[str] = None          # set by the Queen after authentication
    received_at: Optional[datetime] = None   # set by the Queen's clock at ingest

    @property
    def lane(self) -> str:
        if self.kind is SignalKind.REQUEST:
            return "request"
        if self.kind is SignalKind.DRIFT:
            return "drift"
        return "fault"

    def fingerprint(self) -> str:
        """Identical fingerprints inside the dedup window are the same fact."""
        if self.kind is SignalKind.REQUEST:
            return f"request|{self.id}"      # humans are never deduplicated
        if self.kind is SignalKind.DRIFT:
            return f"drift|{self.device}|{self.payload.get('drift_hash', '')}"
        # severity and state included: DOWN (sev 3) and UP (sev 5) are different facts
        parts = [self.kind.value, self.device or self.source, self.mnemonic or "",
                 str(self.severity), str(self.payload.get("trap", "")),
                 str(self.payload.get("interface", "")), str(self.payload.get("state", ""))]
        return "|".join(parts)


class Seen(BaseModel):
    """Dedup / storm memory for one fingerprint. Lives on the board, not in the Queen."""
    fingerprint: str
    window_start: datetime
    last_seen: datetime
    count: int = 1


class Incident(BaseModel):
    id: str = Field(default_factory=new_id)
    lane: str
    key: str
    devices: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)   # capped, see order.py
    first_seen: datetime
    last_seen: datetime
    signal_count: int = 0
    suppressed: int = 0                      # duplicates absorbed without new work
    storm: bool = False
    priority: int = 2
    skills: dict[str, str] = Field(default_factory=dict)  # skill -> directive id
    findings: list[dict[str, Any]] = Field(default_factory=list)   # from tshoot drones
    closed: bool = False

    @property
    def domain(self) -> Optional[str]:
        """'rep-segment:1' for key 'fault:rep-segment:1'; None for request/drift."""
        return self.key.split(":", 1)[1] if self.lane == "fault" else None


class IncidentKey(BaseModel):
    key: str
    incident_id: str


# --------------------------------------------------------------------------- domains
# Failure-domain types that serialize changes: one change at a time per value.
LOCKING_DOMAINS = ("rep-segment", "ptp-domain")


class DeviceDomains(BaseModel):
    """Published by a Map drone (topology.map). e.g.
    {"rep-segment": "1", "ptp-domain": "0", "site": "plant-a"}"""
    device: str
    domains: dict[str, str] = Field(default_factory=dict)
    ports: dict[str, str] = Field(default_factory=dict)  # "Gi1/0/1" -> "rep-segment:1"
    platform: Optional[str] = None
    mapped_by: str
    mapped_at: datetime

    def keys(self) -> set[str]:
        return {f"device:{self.device}"} | {f"{k}:{v}" for k, v in self.domains.items()}


# --------------------------------------------------------------------------- drones
class DroneManifest(BaseModel):
    """What a drone declares when it joins. Skills are dotted: 'tshoot.rep'."""
    designation: str
    skills: list[str]
    platforms: list[str] = Field(default_factory=list)    # empty = any platform
    capacity: int = 1                                     # concurrent directives


class DroneStatus(str, Enum):
    ACTIVE = "active"
    SEVERED = "severed"                      # cut off until regeneration


class Drone(BaseModel):
    manifest: DroneManifest
    status: DroneStatus = DroneStatus.ACTIVE
    registered_at: datetime
    last_seen: datetime
    consecutive_failures: int = 0
    severed_until: Optional[datetime] = None
    completed: int = 0
    failed: int = 0

    @property
    def designation(self) -> str:
        return self.manifest.designation


# --------------------------------------------------------------------------- directives
class Risk(str, Enum):
    READ_ONLY = "read_only"
    CHANGE = "change"


class DirectiveState(str, Enum):
    PENDING = "pending"          # waiting on dependencies
    READY = "ready"              # routable
    ASSIGNED = "assigned"        # the Queen chose a drone; drone has not picked it up
    LEASED = "leased"            # drone is working on it
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_DIRECTIVE = {DirectiveState.PENDING, DirectiveState.READY,
                    DirectiveState.ASSIGNED, DirectiveState.LEASED}
DEAD_DIRECTIVE = {DirectiveState.FAILED, DirectiveState.CANCELLED}


class Directive(BaseModel):
    """One unit of work the Queen hands one drone."""
    id: str = Field(default_factory=new_id)
    incident_id: Optional[str] = None
    skill: str
    device: Optional[str] = None
    platform: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = 2                        # 0 critical .. 3 low
    risk: Risk = Risk.READ_ONLY
    change_id: Optional[str] = None          # set on config.apply / verify.change
    depends_on: list[str] = Field(default_factory=list)
    state: DirectiveState = DirectiveState.PENDING
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None
    lease_expires: Optional[datetime] = None
    attempts: int = 0
    tried_by: list[str] = Field(default_factory=list)
    not_before: Optional[datetime] = None
    unroutable_since: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    def dedup_key(self) -> str:
        return "|".join([self.skill, self.device or "", stable_hash(self.params)])


# --------------------------------------------------------------------------- state
class DesiredState(BaseModel):
    """What SHOULD be on the device. Published by a drone with intent.publish."""
    device: str
    config: dict[str, Any]                   # normalized key -> value
    source: str                              # e.g. github:netops/intent@a1b2c3:ie-sw2.yaml
    version: str
    published_by: str
    published_at: datetime
    strict: bool = False                     # True: keys not in desired count as drift


class ObservedState(BaseModel):
    """What IS on the device, as last collected."""
    device: str
    config: dict[str, Any]
    collected_by: str
    collected_at: datetime


class DriftReport(BaseModel):
    device: str
    missing: dict[str, Any] = Field(default_factory=dict)       # desired, absent on device
    changed: dict[str, dict[str, Any]] = Field(default_factory=dict)  # key -> {desired, observed}
    unexpected: dict[str, Any] = Field(default_factory=dict)    # strict mode only
    desired_version: str = ""
    source: str = ""

    @property
    def empty(self) -> bool:
        return not (self.missing or self.changed or self.unexpected)

    @property
    def digest(self) -> str:
        return stable_hash([self.missing, self.changed, self.unexpected, self.desired_version])


class DriftRecord(BaseModel):
    """The Queen's memory of drift on one device, so she doesn't nag."""
    device: str
    due: bool = True                         # check at next sweep
    last_checked: Optional[datetime] = None
    in_drift: bool = False
    last_digest: Optional[str] = None
    last_signaled_digest: Optional[str] = None
    last_signaled_at: Optional[datetime] = None


# --------------------------------------------------------------------------- consent
class ChangeState(str, Enum):
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


OPEN_CHANGE = {ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL,
               ChangeState.APPROVED, ChangeState.APPLYING}


class Vote(BaseModel):
    principal: str
    decision: Literal["approve", "reject"]
    content_hash: str                        # a vote is for THIS exact config, nothing else
    note: str = ""
    ts: datetime


class Change(BaseModel):
    id: str = Field(default_factory=new_id)
    device: str
    config: str                              # rendered config lines to apply
    source: str                              # where the desired state came from
    summary: str = ""
    content_hash: str
    proposed_by: str                         # drone designation
    requested_by: Optional[str] = None       # human principal, if a human asked
    incident_id: Optional[str] = None
    state: ChangeState = ChangeState.VERIFYING
    verified: Optional[bool] = None
    verification: dict[str, Any] = Field(default_factory=dict)
    votes: list[Vote] = Field(default_factory=list)
    verify_directive_id: Optional[str] = None
    apply_directive_id: Optional[str] = None
    reason: str = ""
    break_glass: bool = False                # skips window + incident freeze, never the freeze switch
    rollout: Optional[str] = None            # changes from the same intent version roll out together
    applied_at: Optional[datetime] = None
    rollback: list[str] = Field(default_factory=list)   # from the guardian, for the record
    created_at: datetime
    updated_at: datetime


class Governance(BaseModel):
    """The freeze switch. One document, on the board, so every Queen honours it."""
    frozen: bool = False
    by: Optional[str] = None
    reason: str = ""
    since: Optional[datetime] = None


class ChangeWindow(BaseModel):
    """Days 0=Mon..6=Sun. Overnight windows (22:00-04:00) are supported."""
    days: list[int] = Field(default_factory=lambda: list(range(7)))
    start: str = "00:00"
    end: str = "23:59"
    tz: str = "UTC"


# --------------------------------------------------------------------------- audit
class AuditRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    ts: datetime
    actor: str
    action: str
    subject: Optional[str] = None
    detail: str = ""


class LeaderLease(BaseModel):
    name: str
    holder: str
    expires: datetime


# --------------------------------------------------------------------------- config
class QueenConfig(BaseModel):
    tick_s: float = 0.2
    leader_ttl_s: float = 10.0               # a Queen that stops renewing is replaced after this
    # order
    dedup_window_s: float = 30.0
    storm_window_s: float = 60.0
    storm_threshold: int = 10
    correlation_window_s: float = 120.0
    # dispatch
    lease_ttl_s: float = 120.0
    assign_timeout_s: float = 30.0           # assigned drone must pick up within this
    max_attempts: int = 3
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 300.0
    device_concurrency: int = 2
    unroutable_timeout_s: float = 600.0
    # registry
    drone_ttl_s: float = 60.0                # no heartbeat for this long = offline
    sever_after: int = 3                     # consecutive failures before severing
    sever_s: float = 300.0                   # how long a severed drone stays cut off
    # drift
    drift_interval_s: float = 60.0
    observed_ttl_s: float = 900.0            # older observed state is re-collected first
    drift_resignal_s: float = 3600.0         # same unchanged drift is re-raised at most this often
    # governance
    change_windows: list[ChangeWindow] = Field(default_factory=list)   # empty = always open
    canary_soak_s: float = 300.0             # wait after the first change of a rollout lands
