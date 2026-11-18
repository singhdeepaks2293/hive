"""drones/intent.py -- the Intent unimatrix: holds what the network should be.

  intent.publish   watch the intent repo; publish desired state on every new version
  drift.remediate  render CLI that turns observed into desired -> proposal
  config.propose   a human asked for a change:
                     payload.intent_patch      -> open a pull request (GitOps, preferred)
                     payload.break_glass+lines -> direct proposal (emergency only)

Proposals still go through guardian verification, consent and governance.
"""
from __future__ import annotations

from typing import Any, Optional

from netskills import normalize
from queen.models import Directive, DriftReport

from .base import Drone, DroneFailure, handles
from .intent_repo import IntentRepo


class IntentDrone(Drone):
    unimatrix = "intent"

    def __init__(self, *args: Any, repo: IntentRepo, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.repo = repo
        self.published: Optional[str] = None

    async def patrol(self) -> None:
        await self.sync()

    async def sync(self, force: bool = False) -> Optional[str]:
        sha = self.repo.head()
        if sha == self.published and not force:
            return None
        for device, doc in self.repo.devices().items():
            await self.link.publish_desired(self.designation, device, doc["config"],
                                            source=self.repo.source(sha), version=sha,
                                            strict=bool(doc.get("strict", False)))
        self.published = sha
        return sha

    @handles("intent.publish")
    async def publish(self, d: Directive) -> dict[str, Any]:
        await self.sync(force=True)
        return {"published": self.published}

    @handles("drift.remediate")
    async def remediate(self, d: Directive) -> dict[str, Any]:
        report = DriftReport(**d.params["drift"])
        if report.source != self.repo.source():
            raise DroneFailure("drift report is for an older intent version; "
                               "the next sweep re-raises it against the current one",
                               retryable=False)
        lines = normalize.remediation(report.missing, report.changed, report.unexpected)
        if not lines:
            raise DroneFailure("drift report renders to no config", retryable=False)
        keys = sorted([*report.missing, *report.changed, *report.unexpected])
        return {"proposal": {
            "device": report.device, "config": lines, "source": report.source,
            "summary": f"restore {len(keys)} setting(s) to intent: {', '.join(keys)}"}}

    @handles("config.propose")
    async def propose(self, d: Directive) -> dict[str, Any]:
        payload = d.params.get("payload") or {}
        who = d.params.get("requested_by")
        if not who:
            raise DroneFailure("change requests must come from an authenticated human",
                               retryable=False)
        if payload.get("intent_patch"):
            pr = self.repo.open_pr(who, d.params.get("request", ""), payload["intent_patch"])
            return {"pull_request": {"id": pr.id, "url": pr.url, "author": who,
                                     "devices": sorted(pr.patch)}}
        if payload.get("break_glass") and payload.get("lines") and d.device:
            return {"proposal": {
                "device": d.device, "config": list(payload["lines"]), "break_glass": True,
                "source": f"break-glass:{who}:{d.id}",
                "summary": f"BREAK-GLASS by {who}: {d.params.get('request', '')}"}}
        raise DroneFailure("change requests need payload.intent_patch (or break_glass with "
                           "lines); free text needs an NLU front-end to build the patch",
                           retryable=False)
