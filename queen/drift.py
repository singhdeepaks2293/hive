"""queen/drift.py -- holding the line on desired state.

Desired state is owned by drones with the `intent.publish` skill (they read it
from GitHub or any other source of truth). Observed state comes back from
drones running `collect.config`. Both are normalized key -> value dicts; the
Queen compares keys and values only and never interprets protocol meaning.

    sweep:
      no observed / observed too old  -> ask for collect.config
      observed fresh, differs         -> drift signal (goes to drift.remediate)
      observed fresh, matches         -> converged

The Queen does not nag: an unchanged drift is re-raised at most every
drift_resignal_s, and never while a change for that device is still open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from .audit import Audit
from .blackboard import Blackboard
from .models import DesiredState, DriftRecord, DriftReport, ObservedState, QueenConfig


def compare(desired: DesiredState, observed: ObservedState) -> DriftReport:
    report = DriftReport(device=desired.device, desired_version=desired.version,
                         source=desired.source)
    for key, want in desired.config.items():
        if key not in observed.config:
            report.missing[key] = want
        elif observed.config[key] != want:
            report.changed[key] = {"desired": want, "observed": observed.config[key]}
    if desired.strict:
        for key, have in observed.config.items():
            if key not in desired.config:
                report.unexpected[key] = have
    return report


@dataclass
class SweepResult:
    collect: list[str] = field(default_factory=list)          # devices to re-observe
    drifted: list[DriftReport] = field(default_factory=list)  # to raise as signals
    converged: list[str] = field(default_factory=list)


class DriftWatch:
    def __init__(self, board: Blackboard, cfg: QueenConfig,
                 clock: Callable[[], datetime], audit: Audit) -> None:
        self.board = board
        self.cfg = cfg
        self.clock = clock
        self.audit = audit
        self._last_sweep: Optional[datetime] = None

    async def publish_desired(self, device: str, config: dict[str, Any], source: str,
                              version: str, published_by: str, strict: bool = False) -> DesiredState:
        state = DesiredState(device=device, config=config, source=source, version=version,
                             published_by=published_by, published_at=self.clock(),
                             strict=strict)
        await self.board.put("desired", device, state)
        await self._mark_due(device)
        await self.audit("desired_published", device, f"{source} v={version} by {published_by}")
        return state

    async def record_observed(self, device: str, config: dict[str, Any],
                              collected_by: str) -> None:
        await self.board.put("observed", device, ObservedState(
            device=device, config=config, collected_by=collected_by,
            collected_at=self.clock()))
        await self._mark_due(device)

    async def report(self, device: str) -> Optional[DriftReport]:
        desired = await self.board.get("desired", device, DesiredState)
        observed = await self.board.get("observed", device, ObservedState)
        if desired is None or observed is None:
            return None
        return compare(desired, observed)

    async def _mark_due(self, device: str) -> None:
        rec = await self.board.get("drift", device, DriftRecord) or DriftRecord(device=device)
        rec.due = True
        await self.board.put("drift", device, rec)

    async def note_signaled(self, report: DriftReport) -> None:
        rec = await self.board.get("drift", report.device, DriftRecord) \
            or DriftRecord(device=report.device)
        rec.last_signaled_digest = report.digest
        rec.last_signaled_at = self.clock()
        await self.board.put("drift", report.device, rec)

    async def sweep(self, devices_with_open_change: set[str]) -> SweepResult:
        now = self.clock()
        periodic = (self._last_sweep is None or
                    now - self._last_sweep >= timedelta(seconds=self.cfg.drift_interval_s))
        if periodic:
            self._last_sweep = now
        out = SweepResult()
        for desired in await self.board.scan("desired", DesiredState):
            dev = desired.device
            rec = await self.board.get("drift", dev, DriftRecord) or DriftRecord(device=dev)
            if not (periodic or rec.due):
                continue
            rec.due = False
            rec.last_checked = now
            observed = await self.board.get("observed", dev, ObservedState)
            stale = (observed is None or now - observed.collected_at
                     > timedelta(seconds=self.cfg.observed_ttl_s))
            if stale:
                out.collect.append(dev)
                await self.board.put("drift", dev, rec)
                continue
            report = compare(desired, observed)
            if report.empty:
                if rec.in_drift:
                    out.converged.append(dev)
                    await self.audit("drift_converged", dev, f"matches {desired.source}")
                rec.in_drift, rec.last_digest = False, None
            else:
                rec.in_drift, rec.last_digest = True, report.digest
                repeat = (rec.last_signaled_digest == report.digest
                          and rec.last_signaled_at is not None
                          and now - rec.last_signaled_at
                          < timedelta(seconds=self.cfg.drift_resignal_s))
                if dev not in devices_with_open_change and not repeat:
                    out.drifted.append(report)
            await self.board.put("drift", dev, rec)
        return out
