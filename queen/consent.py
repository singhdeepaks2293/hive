"""queen/consent.py -- nothing writes to a device without human consent.

Rules, all enforced here:
  - identity comes from an Authenticator, never from a string someone typed
  - only principals with the approver role count
  - a vote is bound to the change's content hash; a different config needs new votes
  - the person who asked for a change cannot approve it
  - verification (guardian) must PASS before approvals can take effect
  - any reject vetoes; votes go stale after vote_ttl_s; changes expire after change_ttl_s
  - a merged pull request counts as approval, but only for reviewers the
    ApprovalSource verified with the Git provider, and never the PR author

The gate is pure logic over Change objects. queen.py persists them.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Callable, Optional, Protocol

from pydantic import BaseModel, Field

from .models import Change, ChangeState, Vote, stable_hash


class ConsentError(Exception):
    """A vote or proposal the gate refuses. The message is safe to show the user."""


class Principal(BaseModel):
    name: str
    roles: set[str] = Field(default_factory=set)


class Authenticator(Protocol):
    def verify(self, token: str) -> Optional[Principal]: ...
    def lookup(self, name: str) -> Optional[Principal]: ...


class HmacAuthenticator:
    """Dev / lab authenticator: tokens are 'name.<hmac>' signed with a shared secret.
    In production, replace with the chat platform's verified identity (Slack
    signed requests, SSO / OIDC) behind the same two methods."""

    def __init__(self, secret: bytes, users: dict[str, set[str]]) -> None:
        if len(secret) < 16:
            raise ValueError("secret must be at least 16 bytes")
        self._secret = secret
        self._users = {name: set(roles) for name, roles in users.items()}

    def issue(self, name: str) -> str:
        if name not in self._users:
            raise KeyError(f"unknown user {name}")
        return f"{name}.{self._sign(name)}"

    def verify(self, token: str) -> Optional[Principal]:
        name, _, sig = token.rpartition(".")
        if not name or name not in self._users:
            return None
        if not hmac.compare_digest(sig, self._sign(name)):
            return None
        return Principal(name=name, roles=set(self._users[name]))

    def lookup(self, name: str) -> Optional[Principal]:
        roles = self._users.get(name)
        return Principal(name=name, roles=set(roles)) if roles is not None else None

    def _sign(self, name: str) -> str:
        return hmac.new(self._secret, name.encode(), hashlib.sha256).hexdigest()[:32]


class PullRequestApproval(BaseModel):
    reviewer: str
    author: str
    merged: bool


class ApprovalSource(Protocol):
    """Asks the Git provider (not the intent drone) who approved the change that
    produced an intent version. Source refs look like 'git:<repo>@<sha>'."""
    def approvals(self, source: str) -> list[PullRequestApproval]: ...


class ConsentPolicy(BaseModel):
    approver_role: str = "netops-approver"
    approvals_required: int = 1
    forbid_self_approval: bool = True
    vote_ttl_s: float = 3600.0
    change_ttl_s: float = 86400.0


def content_hash(device: str, config: str, source: str) -> str:
    return stable_hash({"device": device, "config": config, "source": source})


class ConsentGate:
    def __init__(self, policy: ConsentPolicy, clock: Callable[[], datetime],
                 authenticator: Authenticator,
                 approvals: Optional[ApprovalSource] = None) -> None:
        self.policy = policy
        self.clock = clock
        self.auth = authenticator
        self.approval_source = approvals

    def open(self, *, device: str, config: str, source: str, summary: str, proposed_by: str,
             requested_by: Optional[str], incident_id: Optional[str],
             break_glass: bool = False, rollout: Optional[str] = None) -> Change:
        if not config.strip():
            raise ConsentError("empty proposal")
        now = self.clock()
        return Change(device=device, config=config, source=source, summary=summary,
                      content_hash=content_hash(device, config, source),
                      proposed_by=proposed_by, requested_by=requested_by,
                      incident_id=incident_id, break_glass=break_glass, rollout=rollout,
                      created_at=now, updated_at=now)

    def record_verification(self, change: Change, passed: bool, evidence: dict,
                            rollback: list[str]) -> None:
        change.verified = bool(passed)
        change.verification = evidence
        change.rollback = rollback
        change.updated_at = self.clock()

    def vote(self, change: Change, principal: Principal, decision: str,
             voted_hash: str, note: str = "") -> None:
        if change.state not in (ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL):
            raise ConsentError(f"change {change.id} is {change.state.value}; voting is closed")
        if self.policy.approver_role not in principal.roles:
            raise ConsentError(f"{principal.name} lacks role {self.policy.approver_role}")
        if decision not in ("approve", "reject"):
            raise ConsentError("decision must be approve or reject")
        if not hmac.compare_digest(voted_hash, change.content_hash):
            raise ConsentError("vote is for different content; review the current proposal")
        if (decision == "approve" and self.policy.forbid_self_approval
                and change.requested_by == principal.name):
            raise ConsentError(f"{principal.name} requested this change and cannot approve it")
        change.votes.append(Vote(principal=principal.name, decision=decision,
                                 content_hash=voted_hash, note=note, ts=self.clock()))
        change.updated_at = self.clock()

    def import_pr_approvals(self, change: Change) -> int:
        """Turn verified, merged PR reviews into votes. Returns how many were added."""
        if self.approval_source is None or not change.source.startswith("git:"):
            return 0
        if change.state not in (ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL):
            return 0
        added = 0
        have = {v.principal for v in change.votes}
        for pr in self.approval_source.approvals(change.source):
            name = f"git:{pr.reviewer}"
            if not pr.merged or name in have or pr.reviewer == pr.author:
                continue
            principal = self.auth.lookup(pr.reviewer)
            if principal is None or self.policy.approver_role not in principal.roles:
                continue
            if change.requested_by is None:
                change.requested_by = pr.author          # the PR author asked for it
            change.votes.append(Vote(principal=name, decision="approve",
                                     content_hash=change.content_hash,
                                     note="approved pull request", ts=self.clock()))
            have.add(name)
            added += 1
        return added

    def evaluate(self, change: Change) -> Optional[str]:
        """Advance VERIFYING / AWAITING_APPROVAL. Returns a reason when state changed."""
        if change.state not in (ChangeState.VERIFYING, ChangeState.AWAITING_APPROVAL):
            return None
        now = self.clock()
        if now - change.created_at > timedelta(seconds=self.policy.change_ttl_s):
            return self._to(change, ChangeState.EXPIRED, "not approved in time")
        if change.verified is False:
            return self._to(change, ChangeState.REJECTED, "verification failed")
        latest: dict[str, Vote] = {}
        for v in sorted(change.votes, key=lambda v: v.ts):
            if (v.content_hash == change.content_hash
                    and now - v.ts <= timedelta(seconds=self.policy.vote_ttl_s)):
                latest[v.principal.removeprefix("git:")] = v   # one vote per person
        if any(v.decision == "reject" for v in latest.values()):
            who = ", ".join(n for n, v in latest.items() if v.decision == "reject")
            return self._to(change, ChangeState.REJECTED, f"vetoed by {who}")
        if change.verified is not True:
            return None                                  # votes wait for verification
        approvers = [n for n, v in latest.items() if v.decision == "approve"
                     and not (self.policy.forbid_self_approval and n == change.requested_by)]
        if len(approvers) >= self.policy.approvals_required:
            return self._to(change, ChangeState.APPROVED, f"approved by {', '.join(approvers)}")
        if change.state is ChangeState.VERIFYING:
            return self._to(change, ChangeState.AWAITING_APPROVAL, "verified; awaiting approval")
        return None

    def _to(self, change: Change, state: ChangeState, reason: str) -> str:
        change.state = state
        change.reason = reason
        change.updated_at = self.clock()
        return reason
