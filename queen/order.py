"""queen/order.py -- chaos in, order out.

Every signal goes through `absorb`:
  1. dedup   same fingerprint inside dedup_window_s is suppressed (counted, no new work)
  2. storm   storm_threshold repeats inside storm_window_s flag the incident as a storm
  3. correlate  signals sharing a correlation key inside correlation_window_s join
                one incident. The key is the FAILURE DOMAIN, from the Map drones:
                a REP ring failure seen by four switches is ONE incident.

All memory (Seen, Incident, IncidentKey) is on the blackboard so a new Queen
continues exactly where the old one stopped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from .blackboard import Blackboard
from .models import DeviceDomains, Incident, IncidentKey, QueenConfig, Seen, Signal, SignalKind

MAX_SIGNAL_IDS = 200        # incidents keep a bounded tail of signal ids


def correlation_key(sig: Signal, domains: Optional[DeviceDomains]) -> str:
    if sig.kind is SignalKind.REQUEST:
        return f"request:{sig.id}"
    if sig.kind is SignalKind.DRIFT:
        return f"drift:{sig.device}"
    member = domains.domains if domains else {}
    port_domain = (domains.ports.get(str(sig.payload.get("interface", "")))
                   if domains else None)
    if port_domain:
        return f"fault:{port_domain}"                # a ring port going down IS the ring incident
    mnemonic = sig.mnemonic or ""
    if sig.protocol == "rep" or mnemonic.startswith("REP-"):
        segment = sig.payload.get("segment", member.get("rep-segment"))
        if segment is not None:
            return f"fault:rep-segment:{segment}"
    if sig.protocol == "ptp" or mnemonic.startswith("PTP-"):
        ptp = sig.payload.get("ptp_domain", member.get("ptp-domain"))
        if ptp is not None:
            return f"fault:ptp-domain:{ptp}"
    return f"fault:device:{sig.device or sig.source}"


@dataclass
class Absorbed:
    incident: Incident
    new_incident: bool
    suppressed: bool         # duplicate: no planning needed
    storm_started: bool      # this signal tipped the incident into a storm


class Orderer:
    def __init__(self, board: Blackboard, cfg: QueenConfig,
                 clock: Callable[[], datetime]) -> None:
        self.board = board
        self.cfg = cfg
        self.clock = clock

    async def absorb(self, sig: Signal, domains: Optional[DeviceDomains] = None) -> Absorbed:
        t = sig.received_at or self.clock()

        # 1+2: dedup and storm memory per fingerprint
        fp = sig.fingerprint()
        seen = await self.board.get("seen", fp, Seen)
        suppressed = False
        if seen is None:
            seen = Seen(fingerprint=fp, window_start=t, last_seen=t)
        else:
            suppressed = t - seen.last_seen < timedelta(seconds=self.cfg.dedup_window_s)
            if t - seen.window_start <= timedelta(seconds=self.cfg.storm_window_s):
                seen.count += 1
            else:
                seen.window_start, seen.count = t, 1
            seen.last_seen = t
        await self.board.put("seen", fp, seen)

        # 3: correlation into an open incident
        key = correlation_key(sig, domains)
        incident, new = None, False
        ref = await self.board.get("incident_keys", key, IncidentKey)
        if ref is not None:
            incident = await self.board.get("incidents", ref.incident_id, Incident)
            if (incident is None or incident.closed
                    or t - incident.last_seen > timedelta(seconds=self.cfg.correlation_window_s)):
                incident = None
        if incident is None:
            incident = Incident(lane=sig.lane, key=key, first_seen=t, last_seen=t)
            new = True
            await self.board.put("incident_keys", key,
                                 IncidentKey(key=key, incident_id=incident.id))

        incident.last_seen = t
        incident.signal_count += 1
        if suppressed:
            incident.suppressed += 1
        dev = sig.device or (sig.source if sig.lane == "fault" else None)
        if dev and dev not in incident.devices:
            incident.devices.append(dev)
        incident.signal_ids = (incident.signal_ids + [sig.id])[-MAX_SIGNAL_IDS:]

        storm_started = False
        if (sig.kind in (SignalKind.SYSLOG, SignalKind.TRAP) and not incident.storm
                and seen.count >= self.cfg.storm_threshold):
            incident.storm = True
            incident.priority = 0
            storm_started = True

        await self.board.put("incidents", incident.id, incident)
        return Absorbed(incident, new, suppressed, storm_started)

    async def housekeeping(self, active_directive_ids: set[str]) -> list[Incident]:
        """Close quiet incidents with no live work; forget old fingerprints."""
        now = self.clock()
        quiet = timedelta(seconds=self.cfg.correlation_window_s)
        closed: list[Incident] = []
        for inc in await self.board.scan("incidents", Incident):
            if inc.closed or now - inc.last_seen <= quiet:
                continue
            if any(did in active_directive_ids for did in inc.skills.values()):
                continue
            inc.closed = True
            await self.board.put("incidents", inc.id, inc)
            ref = await self.board.get("incident_keys", inc.key, IncidentKey)
            if ref is not None and ref.incident_id == inc.id:
                await self.board.delete("incident_keys", inc.key)
            closed.append(inc)
        horizon = timedelta(seconds=max(self.cfg.dedup_window_s, self.cfg.storm_window_s) * 10)
        for s in await self.board.scan("seen", Seen):
            if now - s.last_seen > horizon:
                await self.board.delete("seen", s.fingerprint)
        return closed
