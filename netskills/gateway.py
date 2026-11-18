"""netskills/gateway.py -- the only door to devices.

Drones never hold a device session. They get a BoundGateway for their own
designation, and every call goes through PolicyGateway:

  - show commands must fully match the allowlist (no pipes, no newlines)
  - writes (apply / confirm / rollback) only for designations in `writers`
    (the Deployer), and every write must arm a rollback timer
  - one session per device at a time (per-device lock)
  - every call is recorded in the gateway audit log

Row contract: run_show returns parsed rows (TextFSM style). The field names
the drones rely on are documented on each command in ROW_CONTRACT; real
templates (written by the Builder) must produce the same fields.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from . import normalize

ROW_CONTRACT = {
    "show version": "hostname, platform, version, serial",
    "show rep topology": "segment, bridge, port, role (Open | Alt | Fail)",
    "show rep topology detail": "same as show rep topology",
    "show ptp clock": "domain, state (LOCKED | HOLDOVER | FREERUN | ACQUIRING), offset_ns",
    "show ptp parent": "parent",
    "show interfaces": "interface, link_status, protocol_status, input_errors, crc",
}

DEFAULT_ALLOWLIST = [
    r"show version",
    r"show inventory",
    r"show running-config",
    r"show interfaces( [A-Za-z]+\d+(/\d+)*)?",
    r"show rep topology( detail)?",
    r"show ptp (clock|parent|port)",
    r"show logging",
    r"show (lldp|cdp) neighbors( detail)?",
    r"show spanning-tree( summary)?",
    r"show ip (route|bgp summary|ospf neighbor)",
]


class Gateway(Protocol):
    async def run_show(self, device: str, command: str) -> list[dict[str, Any]]: ...
    async def get_running_text(self, device: str) -> str: ...
    async def get_config(self, device: str) -> dict[str, Any]: ...
    async def apply(self, device: str, lines: list[str], revert_minutes: int) -> str: ...
    async def confirm(self, device: str, checkpoint: str) -> None: ...
    async def rollback(self, device: str, checkpoint: str) -> None: ...


@dataclass
class GatewayCall:
    ts: datetime
    caller: str
    device: str
    action: str
    detail: str
    allowed: bool


@dataclass
class PolicyGateway:
    inner: Gateway
    writers: set[str]
    allowlist: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWLIST))
    max_revert_minutes: int = 15
    log: list[GatewayCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._allow = [re.compile(rf"^{p}$") for p in self.allowlist]
        self._locks: dict[str, asyncio.Lock] = {}

    def for_drone(self, designation: str) -> "BoundGateway":
        return BoundGateway(self, designation)

    def _lock(self, device: str) -> asyncio.Lock:
        return self._locks.setdefault(device, asyncio.Lock())

    def _record(self, caller: str, device: str, action: str, detail: str, allowed: bool) -> None:
        self.log.append(GatewayCall(datetime.now(timezone.utc), caller, device, action,
                                    detail[:200], allowed))

    def _check_show(self, caller: str, device: str, command: str) -> None:
        ok = ("\n" not in command and "|" not in command
              and any(p.match(command.strip()) for p in self._allow))
        self._record(caller, device, "show", command, ok)
        if not ok:
            raise PermissionError(f"command not allowlisted: {command!r}")

    def _check_write(self, caller: str, device: str, action: str, detail: str) -> None:
        ok = caller in self.writers
        self._record(caller, device, action, detail, ok)
        if not ok:
            raise PermissionError(f"{caller} may not write to devices")

    async def run_show(self, caller: str, device: str, command: str) -> list[dict[str, Any]]:
        self._check_show(caller, device, command)
        async with self._lock(device):
            return await self.inner.run_show(device, command.strip())

    async def get_running_text(self, caller: str, device: str) -> str:
        self._check_show(caller, device, "show running-config")
        async with self._lock(device):
            return await self.inner.get_running_text(device)

    async def get_config(self, caller: str, device: str) -> dict[str, Any]:
        self._check_show(caller, device, "show running-config")
        async with self._lock(device):
            return await self.inner.get_config(device)

    async def apply(self, caller: str, device: str, lines: list[str],
                    revert_minutes: int) -> str:
        detail = f"{len(lines)} lines, revert {revert_minutes}m"
        if not 1 <= revert_minutes <= self.max_revert_minutes:
            self._record(caller, device, "apply", detail, False)
            raise PermissionError("every write needs a rollback timer of "
                                  f"1..{self.max_revert_minutes} minutes")
        self._check_write(caller, device, "apply", detail)
        async with self._lock(device):
            return await self.inner.apply(device, lines, revert_minutes)

    async def confirm(self, caller: str, device: str, checkpoint: str) -> None:
        self._check_write(caller, device, "confirm", checkpoint)
        async with self._lock(device):
            await self.inner.confirm(device, checkpoint)

    async def rollback(self, caller: str, device: str, checkpoint: str) -> None:
        self._check_write(caller, device, "rollback", checkpoint)
        async with self._lock(device):
            await self.inner.rollback(device, checkpoint)


class BoundGateway:
    """What a drone holds: the policy gateway with its own identity baked in."""

    def __init__(self, policy: PolicyGateway, designation: str) -> None:
        self._p = policy
        self.designation = designation

    async def run_show(self, device: str, command: str) -> list[dict[str, Any]]:
        return await self._p.run_show(self.designation, device, command)

    async def get_running_text(self, device: str) -> str:
        return await self._p.get_running_text(self.designation, device)

    async def get_config(self, device: str) -> dict[str, Any]:
        return await self._p.get_config(self.designation, device)

    async def apply(self, device: str, lines: list[str], revert_minutes: int) -> str:
        return await self._p.apply(self.designation, device, lines, revert_minutes)

    async def confirm(self, device: str, checkpoint: str) -> None:
        await self._p.confirm(self.designation, device, checkpoint)

    async def rollback(self, device: str, checkpoint: str) -> None:
        await self._p.rollback(self.designation, device, checkpoint)


class NetmikoGateway:
    """Real devices, READ-ONLY for now (rollout phases 2-4).

    Credentials come from NET_USER / NET_PASS; the host map comes from
    inventory (NetBox later). Writes raise until phase 5, when they should use
    the platform's rollback timer:
        IOS / IOS-XE / IE:  archive + `configure terminal revert timer <m>` / `configure confirm`
        Junos:              `commit confirmed <m>` / `commit`
        NX-OS:              checkpoint + rollback
    """

    def __init__(self, hosts: dict[str, dict[str, Any]]) -> None:
        self.hosts = hosts                       # device -> {"host": ..., "device_type": ...}

    def _params(self, device: str) -> dict[str, Any]:
        base = self.hosts[device]
        return {"host": base["host"], "device_type": base.get("device_type", "cisco_ios"),
                "username": os.environ["NET_USER"], "password": os.environ["NET_PASS"]}

    def _send(self, device: str, command: str, textfsm: bool) -> Any:
        from netmiko import ConnectHandler      # optional dependency: pip install .[netmiko]
        with ConnectHandler(**self._params(device)) as conn:
            return conn.send_command(command, use_textfsm=textfsm)

    async def run_show(self, device: str, command: str) -> list[dict[str, Any]]:
        out = await asyncio.to_thread(self._send, device, command, True)
        if isinstance(out, list):
            return out
        raise ValueError(f"no TextFSM template parsed {command!r}; ask the Builder for one")

    async def get_running_text(self, device: str) -> str:
        return await asyncio.to_thread(self._send, device, "show running-config", False)

    async def get_config(self, device: str) -> dict[str, Any]:
        text = await self.get_running_text(device)
        return normalize.apply_lines({}, text.splitlines())

    async def apply(self, device: str, lines: list[str], revert_minutes: int) -> str:
        raise NotImplementedError("device writes arrive in rollout phase 5")

    async def confirm(self, device: str, checkpoint: str) -> None:
        raise NotImplementedError("device writes arrive in rollout phase 5")

    async def rollback(self, device: str, checkpoint: str) -> None:
        raise NotImplementedError("device writes arrive in rollout phase 5")


def gateway_summary(policy: PolicyGateway, caller: Optional[str] = None) -> dict[str, int]:
    calls = [c for c in policy.log if caller is None or c.caller == caller]
    return {"calls": len(calls), "denied": sum(1 for c in calls if not c.allowed),
            "writes": sum(1 for c in calls if c.action != "show" and c.allowed)}
