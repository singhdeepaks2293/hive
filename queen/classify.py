"""queen/classify.py -- decide WHAT kind of work a signal needs, as skills.

The classifier never picks a drone. It answers "this needs tshoot.rep after a
collect.show", and dispatch finds the drone. Swap RuleClassifier for an LLM
classifier later; it only has to return list[Need] with the same contract.

Skills are dotted and hierarchical. A drone with `tshoot` can serve
`tshoot.rep`; a drone with `tshoot.rep` is preferred for it. So rules can ask
for the most specific skill and still fall back to generalists.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional, Protocol

from pydantic import BaseModel, Field

from .models import Incident, Risk, Signal, SignalKind


class Need(BaseModel):
    skill: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: Optional[int] = None           # None = incident priority
    risk: Risk = Risk.READ_ONLY
    after: list[int] = Field(default_factory=list)   # indices into the same plan
    device: Optional[str] = None             # None = signal's device


class Classifier(Protocol):
    def plan(self, incident: Incident, signal: Signal) -> list[Need]: ...


# Skills a classifier may never request directly. Changes only come from a
# proposal that passed the consent gate; the Queen creates those herself.
RESERVED_SKILLS = {"config.apply", "verify.change"}


def validate_plan(needs: list[Need]) -> list[Need]:
    for idx, n in enumerate(needs):
        if n.skill in RESERVED_SKILLS or n.risk is Risk.CHANGE:
            raise ValueError(f"classifier asked for {n.skill} ({n.risk.value}); "
                             "changes only come through consent")
        for i in n.after:
            if not 0 <= i < idx:
                raise ValueError(f"need {idx} depends on {i}; must be 0 <= dep < {idx}")
    return needs


# --------------------------------------------------------------------------- fault rules
def _rep(sig: Signal, inc: Incident) -> list[Need]:
    return [
        Need(skill="collect.show", priority=1,
             params={"commands": ["show rep topology", "show rep topology detail"]}),
        Need(skill="tshoot.rep", priority=1, after=[0],
             params={"segment": sig.payload.get("segment"), "mnemonic": sig.mnemonic or "",
                     "skill_refs": ["cisco-ie-rep-configuration"]}),
    ]


def _ptp(sig: Signal, inc: Incident) -> list[Need]:
    return [Need(skill="tshoot.ptp", priority=1,
                 params={"mnemonic": sig.mnemonic or "",
                         "skill_refs": ["cisco-ie-ptp-over-rep"]})]


def _link(sig: Signal, inc: Incident) -> list[Need]:
    iface = str(sig.payload.get("interface", ""))
    down = (str(sig.payload.get("state", "")).lower() == "down"
            or "down" in sig.text.lower() or sig.payload.get("trap") == "linkDown")
    if not down:
        return [Need(skill="collect.show", priority=3,
                     params={"commands": [f"show interfaces {iface}".strip()]})]
    return [Need(skill="tshoot.interface", priority=1,
                 params={"interface": iface, "mnemonic": sig.mnemonic or ""})]


def _neighbor(sig: Signal, inc: Incident) -> list[Need]:
    proto = "bgp" if (sig.mnemonic or "").startswith("BGP") else "ospf"
    return [Need(skill=f"tshoot.routing.{proto}", priority=1,
                 params={"mnemonic": sig.mnemonic or ""})]


def _config_changed(sig: Signal, inc: Incident) -> list[Need]:
    # someone changed the box: back it up and re-observe, so drift watch sees it fast
    return [Need(skill="backup.run", params={"reason": "config_change"}),
            Need(skill="collect.config", params={"reason": "config_change"})]


def _reboot(sig: Signal, inc: Incident) -> list[Need]:
    return [Need(skill="inventory.refresh", priority=1),
            Need(skill="collect.config", after=[0], params={"reason": "reboot"})]


_MNEMONIC_RULES: list[tuple[re.Pattern, Callable[[Signal, Incident], list[Need]]]] = [
    (re.compile(r"^REP-"), _rep),
    (re.compile(r"^PTP-"), _ptp),
    (re.compile(r"^(LINK-3-UPDOWN|LINEPROTO-5-UPDOWN|IFNET-.*DOWN)"), _link),
    (re.compile(r"^(BGP-5-ADJCHANGE|OSPF-5-ADJCHG)"), _neighbor),
    (re.compile(r"^SYS-5-CONFIG_I"), _config_changed),
]

_TRAP_RULES: dict[str, Callable[[Signal, Incident], list[Need]]] = {
    "coldStart": _reboot, "warmStart": _reboot, "linkDown": _link, "linkUp": _link,
}

def _interface_health(sig: Signal, inc: Incident) -> list[Need]:
    return [Need(skill="tshoot.interface", priority=1,
                 params={"interface": sig.payload.get("interface", ""),
                         "check": sig.payload.get("check", "")})]


def _routing(sig: Signal, inc: Incident) -> list[Need]:
    proto = sig.payload.get("routing_protocol", "")
    return [Need(skill=f"tshoot.routing.{proto}" if proto else "tshoot.routing", priority=1,
                 params={"check": sig.payload.get("check", "")})]


# protocol hints: used by Sentinel health signals and as a fallback for syslog
_PROTOCOL_RULES: dict[str, Callable[[Signal, Incident], list[Need]]] = {
    "rep": _rep, "ptp": _ptp, "interface": _interface_health, "routing": _routing,
}

# --------------------------------------------------------------------------- request rules
# word boundaries: "exchange" must not match "change"
# tshoot is checked first on purpose: "why did the change break bgp" is a question,
# and misreading a question as a change is worse than the reverse.
_INTENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:troubleshoot|diagnose|why|debug|investigate|down|broken)\w{0,3}\b"),
     "tshoot"),
    (re.compile(r"\b(?:configure|apply|change|add|remove|deploy|enable|disable)\w{0,3}\b"),
     "change"),
    (re.compile(r"\bbackups?\b"), "backup"),
    (re.compile(r"\b(?:inventory|refresh)\b"), "inventory"),
    (re.compile(r"\b(?:drift|compliance|compliant)\b"), "drift"),
    (re.compile(r"\b(?:template|textfsm)s?\b"), "template"),
    (re.compile(r"\b(?:show|parse|collect)\b"), "collect"),
]
_TOPICS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brep\b"), "rep"),
    (re.compile(r"\bptp\b"), "ptp"),
    (re.compile(r"\bbgp\b"), "routing.bgp"),
    (re.compile(r"\bospf\b"), "routing.ospf"),
    (re.compile(r"\bstatic\b"), "routing.static"),
    (re.compile(r"\b(?:interface|port|link|gi\d\S*)\b"), "interface"),
]


def _topic(text: str) -> Optional[str]:
    for pat, name in _TOPICS:
        if pat.search(text):
            return name
    return None


class RuleClassifier:
    """Deterministic rules. Unknown faults and requests go to generic tshoot."""

    def plan(self, incident: Incident, signal: Signal) -> list[Need]:
        if signal.kind is SignalKind.DRIFT:
            needs = [Need(skill="drift.remediate", params={
                "drift": signal.payload.get("drift", {}),
                "desired_version": signal.payload.get("desired_version", ""),
                "source": signal.payload.get("source", "")})]
        elif signal.kind is SignalKind.REQUEST:
            needs = self._request(signal)
        else:
            needs = self._fault(signal, incident)
        return validate_plan(needs)

    def _fault(self, sig: Signal, inc: Incident) -> list[Need]:
        builder = None
        if sig.kind is SignalKind.TRAP:
            builder = _TRAP_RULES.get(str(sig.payload.get("trap", "")))
        elif sig.mnemonic:
            builder = next((b for p, b in _MNEMONIC_RULES if p.search(sig.mnemonic)), None)
        if builder is None and sig.protocol:
            builder = _PROTOCOL_RULES.get(sig.protocol)
        if builder is not None:
            return builder(sig, inc)
        return [Need(skill="tshoot", params={"mnemonic": sig.mnemonic or "",
                                             "text": sig.text[:500]})]

    def _request(self, sig: Signal) -> list[Need]:
        text = sig.text.lower()
        structured_change = bool(sig.payload.get("intent_patch") or sig.payload.get("break_glass"))
        intent = (str(sig.payload.get("intent", "")).lower()
                  or ("change" if structured_change else "")
                  or next((name for pat, name in _INTENTS if pat.search(text)), "tshoot"))
        topic = _topic(text)
        base = {"request": sig.text, "requested_by": sig.principal, "payload": sig.payload}
        if intent == "change":
            # a human asking for a change gets a PROPOSAL, rendered from the source of truth
            return [Need(skill="config.propose", priority=1, params=base)]
        if intent == "tshoot":
            return [Need(skill=f"tshoot.{topic}" if topic else "tshoot", priority=1, params=base)]
        if intent == "backup":
            return [Need(skill="backup.run", params=base)]
        if intent == "inventory":
            return [Need(skill="inventory.refresh", params=base)]
        if intent == "drift":
            return [Need(skill="collect.config", params={"reason": "drift check requested"})]
        if intent == "template":
            return [Need(skill="template.build", priority=3, params=base)]
        return [Need(skill="collect.show", params={**base,
                                                   "commands": sig.payload.get("commands", [])})]
