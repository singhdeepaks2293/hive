"""netskills/fake.py -- a small simulated plant network for tests and the demo.

Models what the drones reason about: a REP ring (roles Open / Alt / Fail,
segment complete or open), PTP state per switch, interface counters, and the
running config. Faults emit syslog through `sink` (wire it to Queen.ingest),
the way real switches would.

The syslog mnemonics are representative of Cisco IE behaviour, not a
verbatim catalogue. The row fields follow netskills.gateway.ROW_CONTRACT.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from queen.models import Signal, SignalKind

from . import normalize

Port = tuple[str, str]                                 # (device, interface)
Sink = Callable[[Signal], Awaitable[Any]]


@dataclass
class FakeDevice:
    name: str
    config: dict[str, Any]
    site: str = "plant-a"
    platform: str = "cisco_ie"
    serial: str = ""
    ptp_domain: str = "0"
    ptp_state: str = "LOCKED"
    offset_ns: int = 40
    crc: dict[str, int] = field(default_factory=dict)
    break_on_apply: Optional[str] = None               # test hook: next apply breaks this


@dataclass
class RingLink:
    a: Port
    b: Port
    up: bool = True

    def ends(self) -> tuple[Port, Port]:
        return self.a, self.b


class FakeNetwork:
    def __init__(self, sink: Optional[Sink] = None) -> None:
        self.devices: dict[str, FakeDevice] = {}
        self.rings: dict[str, list[RingLink]] = {}
        self.alt: dict[str, Port] = {}
        self.grandmaster: dict[str, str] = {}           # ptp domain -> device
        self.sink = sink
        self.applied: list[tuple[str, list[str]]] = []
        self._snapshots: dict[str, tuple[str, dict[str, Any]]] = {}
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ build
    def add_device(self, device: FakeDevice) -> None:
        device.serial = device.serial or f"FOC{abs(hash(device.name)) % 10**7:07d}"
        self.devices[device.name] = device

    def add_ring(self, segment: str, links: list[tuple[Port, Port]], alt: Port) -> None:
        self.rings[segment] = [RingLink(a, b) for a, b in links]
        self.alt[segment] = alt

    # ------------------------------------------------------------------ state
    def segment_of(self, device: str) -> Optional[str]:
        for seg, links in self.rings.items():
            if any(end[0] == device for link in links for end in link.ends()):
                return seg
        return None

    def segment_complete(self, segment: str) -> bool:
        return all(link.up for link in self.rings[segment])

    def rep_rows(self, segment: str) -> list[dict[str, Any]]:
        complete = self.segment_complete(segment)
        rows = []
        for link in self.rings[segment]:
            for end in link.ends():
                if not link.up:
                    role = "Fail"
                elif complete and end == self.alt[segment]:
                    role = "Alt"
                else:
                    role = "Open"
                rows.append({"segment": segment, "bridge": end[0], "port": end[1], "role": role})
        return rows

    def link_up(self, device: str, port: str) -> bool:
        for links in self.rings.values():
            for link in links:
                if (device, port) in link.ends():
                    return link.up
        return True

    # ------------------------------------------------------------------ faults
    async def _emit(self, device: str, mnemonic: str, text: str, severity: int,
                    **payload: Any) -> None:
        if self.sink is not None:
            await self.sink(Signal(kind=SignalKind.SYSLOG, source=device, device=device,
                                   mnemonic=mnemonic, severity=severity, text=text,
                                   payload=payload))

    def _find(self, device: str, port: str) -> tuple[str, RingLink]:
        for seg, links in self.rings.items():
            for link in links:
                if (device, port) in link.ends():
                    return seg, link
        raise KeyError(f"{device} {port} is not a ring port")

    async def break_link(self, device: str, port: str) -> None:
        seg, link = self._find(device, port)
        link.up = False
        far = link.b if link.a == (device, port) else link.a
        for dev, prt in link.ends():
            await self._emit(dev, "LINK-3-UPDOWN", f"Interface {prt}, changed state to down",
                             3, interface=prt, state="down")
            await self._emit(dev, "REP-4-LINKSTATUS", f"{prt} (segment {seg}) is non-operational",
                             4, interface=prt, segment=seg, state="failed")
        # the far side lost its PTP path until REP reconverges the ring
        far_dev = self.devices[far[0]]
        if self.grandmaster.get(far_dev.ptp_domain) != far_dev.name:
            far_dev.ptp_state, far_dev.offset_ns = "HOLDOVER", 2600
            await self._emit(far_dev.name, "PTP-4-HOLDOVER",
                             f"clock entered holdover, domain {far_dev.ptp_domain}", 4,
                             ptp_domain=far_dev.ptp_domain)

    async def restore_link(self, device: str, port: str) -> None:
        seg, link = self._find(device, port)
        link.up = True
        for dev, prt in link.ends():
            await self._emit(dev, "LINK-3-UPDOWN", f"Interface {prt}, changed state to up",
                             5, interface=prt, state="up")
        for d in self.devices.values():
            if d.ptp_state != "LOCKED":
                d.ptp_state, d.offset_ns = "LOCKED", 40

    async def flap(self, device: str, port: str, times: int) -> None:
        dev = self.devices[device]
        for i in range(times):
            state = "down" if i % 2 == 0 else "up"
            dev.crc[port] = dev.crc.get(port, 0) + 37
            await self._emit(device, "LINK-3-UPDOWN", f"Interface {port}, changed state to {state}",
                             3 if state == "down" else 5, interface=port, state=state)

    # ------------------------------------------------------------------ config
    def apply(self, device: str, lines: list[str]) -> str:
        dev = self.devices[device]
        cp = f"cp-{next(self._ids)}"
        self._snapshots[cp] = (device, dict(dev.config))
        dev.config = normalize.apply_lines(dev.config, lines)
        self.applied.append((device, list(lines)))
        if dev.break_on_apply == "ptp":
            dev.ptp_state, dev.offset_ns = "FREERUN", 90000
            dev.break_on_apply = None
        return cp

    def confirm(self, checkpoint: str) -> None:
        self._snapshots.pop(checkpoint, None)

    def rollback(self, checkpoint: str) -> None:
        device, config = self._snapshots.pop(checkpoint)
        dev = self.devices[device]
        dev.config = config
        dev.ptp_state, dev.offset_ns = "LOCKED", 40


class FakeGateway:
    """Implements netskills.gateway.Gateway over a FakeNetwork."""

    def __init__(self, network: FakeNetwork) -> None:
        self.net = network

    async def run_show(self, device: str, command: str) -> list[dict[str, Any]]:
        net = self.net
        dev = net.devices[device]
        if command == "show version":
            return [{"hostname": dev.name, "platform": dev.platform, "version": "17.9.4",
                     "serial": dev.serial}]
        if command in ("show rep topology", "show rep topology detail"):
            seg = net.segment_of(device)
            return net.rep_rows(seg) if seg else []
        if command == "show ptp clock":
            return [{"domain": dev.ptp_domain, "state": dev.ptp_state,
                     "offset_ns": dev.offset_ns}]
        if command == "show ptp parent":
            return [{"parent": net.grandmaster.get(dev.ptp_domain, "")}]
        if command.startswith("show interfaces"):
            wanted = command[len("show interfaces"):].strip()
            ports = sorted({k.split("|")[0].removeprefix("interface ")
                            for k in dev.config if k.startswith("interface ")} | set(dev.crc))
            rows = []
            for p in ports:
                if wanted and p != wanted:
                    continue
                up = net.link_up(device, p)
                rows.append({"interface": p, "link_status": "up" if up else "down",
                             "protocol_status": "up" if up else "down",
                             "input_errors": dev.crc.get(p, 0), "crc": dev.crc.get(p, 0)})
            return rows
        raise ValueError(f"the fake network does not model {command!r}")

    async def get_running_text(self, device: str) -> str:
        return "\n".join(normalize.render(self.net.devices[device].config))

    async def get_config(self, device: str) -> dict[str, Any]:
        return dict(self.net.devices[device].config)

    async def apply(self, device: str, lines: list[str], revert_minutes: int) -> str:
        return self.net.apply(device, lines)

    async def confirm(self, device: str, checkpoint: str) -> None:
        self.net.confirm(checkpoint)

    async def rollback(self, device: str, checkpoint: str) -> None:
        self.net.rollback(checkpoint)


def plant_network(intent: dict[str, dict[str, Any]], sink: Optional[Sink] = None) -> FakeNetwork:
    """Ring 1 of ie-sw1..ie-sw4 built from intent, ie-sw1 as PTP grandmaster.
    ie-sw2 ships with drift so the collective has something to fix."""
    net = FakeNetwork(sink)
    for name, doc in intent.items():
        net.add_device(FakeDevice(name=name, config=dict(doc["config"]),
                                  site=doc.get("site", "plant-a"),
                                  platform=doc.get("platform", "cisco_ie")))
    if "ie-sw2" in net.devices:
        cfg = net.devices["ie-sw2"].config
        cfg["ntp server"] = "10.0.0.9"                  # someone pointed it at the old server
        cfg.pop("interface Gi1/0/1|description", None)  # and lost a description
    net.add_ring("1", [(("ie-sw1", "Gi1/0/1"), ("ie-sw2", "Gi1/0/2")),
                       (("ie-sw2", "Gi1/0/1"), ("ie-sw3", "Gi1/0/2")),
                       (("ie-sw3", "Gi1/0/1"), ("ie-sw4", "Gi1/0/2")),
                       (("ie-sw4", "Gi1/0/1"), ("ie-sw1", "Gi1/0/2"))],
                 alt=("ie-sw1", "Gi1/0/2"))
    net.grandmaster["0"] = "ie-sw1"
    return net
