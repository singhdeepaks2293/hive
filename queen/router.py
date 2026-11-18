"""queen/router.py -- picks the expert by protocol / task type.

The Router is pure decision logic: Event in, list[TaskSpec] out.
It never touches devices or the blackboard; the Queen publishes what it returns.
`depends_on` holds *local* indices into the same batch (e.g. diagnose waits for
collect); the Queen resolves them to real task ids at publish time.
"""
from __future__ import annotations

import re
from typing import Callable

from .models import Event, EventKind, Risk, TaskSpec

WORKERS = {"scout", "parser", "builder", "analyst", "guardian", "helper", "expert:static"}


# --------------------------------------------------------------------------- builders
def _link(ev: Event) -> list[TaskSpec]:
    iface = str(ev.payload.get("interface", ""))
    down = "down" in ev.text.lower()
    cmds = [f"show interfaces {iface}".strip(), "show logging"]
    specs = [TaskSpec(type="collect", owner="parser", device=ev.device,
                      priority=1 if down else 3, params={"commands": cmds})]
    if down:
        specs.append(TaskSpec(type="diagnose", owner="analyst", device=ev.device, priority=1,
                              params={"focus": "interface_down", "interface": iface},
                              depends_on=[0]))
    return specs


def _rep(ev: Event) -> list[TaskSpec]:
    return [
        TaskSpec(type="collect", owner="parser", device=ev.device, priority=1,
                 params={"commands": ["show rep topology", "show rep topology detail"]}),
        TaskSpec(type="diagnose", owner="analyst", device=ev.device, priority=1,
                 params={"protocol": "rep", "mnemonic": ev.mnemonic or ""},
                 skill_refs=["cisco-ie-rep-configuration"], depends_on=[0]),
    ]


def _ptp(ev: Event) -> list[TaskSpec]:
    return [
        TaskSpec(type="collect", owner="parser", device=ev.device, priority=1,
                 params={"commands": ["show ptp clock", "show ptp port"]}),
        TaskSpec(type="diagnose", owner="analyst", device=ev.device, priority=1,
                 params={"protocol": "ptp", "mnemonic": ev.mnemonic or ""},
                 skill_refs=["cisco-ie-ptp-over-rep"], depends_on=[0]),
    ]


def _neighbor(ev: Event) -> list[TaskSpec]:
    proto = "bgp" if (ev.mnemonic or "").startswith("BGP") else "ospf"
    cmds = ["show ip bgp summary"] if proto == "bgp" else ["show ip ospf neighbor"]
    return [
        TaskSpec(type="collect", owner="parser", device=ev.device, priority=1,
                 params={"commands": cmds}),
        TaskSpec(type="diagnose", owner="analyst", device=ev.device, priority=1,
                 params={"protocol": proto, "mnemonic": ev.mnemonic or ""}, depends_on=[0]),
    ]


def _config_changed(ev: Event) -> list[TaskSpec]:
    return [TaskSpec(type="backup", owner="scout", device=ev.device, priority=2,
                     params={"reason": "config_change"})]


def _reboot(ev: Event) -> list[TaskSpec]:
    return [
        TaskSpec(type="refresh_inventory", owner="scout", device=ev.device, priority=1),
        TaskSpec(type="backup", owner="scout", device=ev.device, priority=2, depends_on=[0]),
    ]


def _triage(ev: Event) -> list[TaskSpec]:
    return [TaskSpec(type="triage", owner="analyst", device=ev.device, priority=2,
                     params={"mnemonic": ev.mnemonic or "", "text": ev.text[:500]})]


_MNEMONIC_RULES: list[tuple[re.Pattern, Callable[[Event], list[TaskSpec]]]] = [
    (re.compile(r"^(LINK-3-UPDOWN|IFNET-.*DOWN)"), _link),
    (re.compile(r"^REP-"), _rep),
    (re.compile(r"^PTP-"), _ptp),
    (re.compile(r"^(BGP-5-ADJCHANGE|OSPF-5-ADJCHG)"), _neighbor),
    (re.compile(r"^SYS-5-CONFIG_I"), _config_changed),
]

_TRAP_RULES: dict[str, Callable[[Event], list[TaskSpec]]] = {
    "coldStart": _reboot, "warmStart": _reboot, "linkDown": _link, "linkUp": _link,
}

# Word-boundary intent match: "exchange" must NOT match "change";
# up to 3 trailing word chars allow plurals/tenses ("routes", "backups").
_INTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:configure|apply|change)\w{0,3}\b"), "change"),
    (re.compile(r"\bbackup\w{0,3}\b"), "backup"),
    (re.compile(r"\b(?:inventory|refresh)\w{0,3}\b"), "inventory"),
    (re.compile(r"\b(?:static|route)\w{0,3}\b"), "static"),
    (re.compile(r"\bparse\w{0,3}\b"), "parse"),
    (re.compile(r"\b(?:template|textfsm)\w{0,3}\b"), "template"),
    (re.compile(r"\b(?:diagnose|troubleshoot|why)\w{0,3}\b"), "diagnose"),
]


class Router:
    """Stateless rules table. Swap for an LLM-assisted router later without
    touching the Queen -- the contract is just `triage` / `route_request`."""

    def triage(self, ev: Event) -> list[TaskSpec]:
        """Device event (syslog / SNMP trap) -> task batch."""
        specs: list[TaskSpec] = []
        if ev.kind is EventKind.SNMP:
            builder = _TRAP_RULES.get(str(ev.payload.get("trap", "")))
            if builder:
                specs = builder(ev)
        elif ev.mnemonic:
            for pattern, builder in _MNEMONIC_RULES:
                if pattern.search(ev.mnemonic):
                    specs = builder(ev)
                    break
        if not specs and ev.protocol == "rep":
            specs = _rep(ev)
        if not specs and ev.protocol == "ptp":
            specs = _ptp(ev)
        if not specs:
            specs = _triage(ev)
        return self._sanitize(specs)

    def route_request(self, ev: Event) -> list[TaskSpec]:
        """Human chat request -> task batch. Deterministic keyword intents for
        phase 1; an NLU front-end can fill `payload['intent']` directly later."""
        intent = str(ev.payload.get("intent", "")).lower()
        if not intent:
            text = ev.text.lower()
            for pattern, name in _INTENT_PATTERNS:
                if pattern.search(text):
                    intent = name
                    break
        dev = ev.device
        if intent == "change":
            specs = [TaskSpec(type="verify_change", owner="guardian", device=dev, priority=1,
                              risk=Risk.CHANGE, params={"request": ev.text})]
        elif intent == "backup":
            specs = [TaskSpec(type="backup", owner="scout", device=dev, priority=2)]
        elif intent == "inventory":
            specs = [TaskSpec(type="refresh_inventory", owner="scout", device=dev, priority=2)]
        elif intent == "static" or ev.protocol == "static":
            specs = [TaskSpec(type="static_check", owner="expert:static", device=dev, priority=2,
                              params={"request": ev.text})]
        elif intent == "parse":
            specs = [TaskSpec(type="parse", owner="parser", device=dev, priority=2,
                              params=ev.payload.get("params", {}))]
        elif intent == "template":
            specs = [TaskSpec(type="build_template", owner="builder", device=dev, priority=3,
                              params={"request": ev.text})]
        elif intent == "diagnose":
            specs = [TaskSpec(type="diagnose", owner="analyst", device=dev, priority=1,
                              params={"request": ev.text})]
        else:
            specs = _triage(ev)
        return self._sanitize(specs)

    @staticmethod
    def _sanitize(specs: list[TaskSpec]) -> list[TaskSpec]:
        for idx, s in enumerate(specs):
            if s.owner not in WORKERS:
                s.owner = "analyst"          # unknown expert -> analyst triage
            for i in s.depends_on:
                # deps must point strictly BACKWARD: no self/forward refs, no cycles
                if not 0 <= i < idx:
                    raise ValueError(
                        f"router bug: spec {idx} depends_on {i}; must be 0 <= dep < {idx}")
        return specs