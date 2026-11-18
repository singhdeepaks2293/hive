"""netskills/normalize.py -- one config shape for intent, observation and drift.

Normalized config is a flat dict:
    top level:   "ntp server"                     -> "10.0.0.1"
    in sections: "interface Gi1/0/2|description"   -> "uplink-core"
    flags:       "interface Gi1/0/3|shutdown"      -> False   (renders "no shutdown")
    bare lines:  "spanning-tree mode rapid-pvst"    -> True

Each key holds ONE value. That fits hostname, descriptions, access VLANs, a
single NTP server. Settings that legitimately repeat (several NTP servers,
several logging hosts) need a key per value ("ntp server|10.0.0.1": True) and
strict mode on the device's intent. That is a known limit of this first cut.

Rendering is straightforward. Parsing needs to know where a setting name ends
and its value begins, so KNOWN_SETTINGS lists the settings this project
manages. Unknown lines are kept whole as True flags, so nothing is lost.
"""
from __future__ import annotations

from typing import Any, Iterable

SECTION_HEADS = ("interface ", "vlan ", "router ", "line ")

TOP_SETTINGS = ["hostname", "ntp server", "logging host", "snmp-server location",
                "snmp-server contact", "ip domain name", "clock timezone", "ptp mode",
                "ptp domain", "spanning-tree mode"]
SECTION_SETTINGS = ["description", "switchport access vlan", "switchport trunk allowed vlan",
                    "switchport mode", "rep segment", "rep preempt delay", "ptp enable",
                    "spanning-tree portfast", "shutdown", "name", "ip address"]
FLAG_SETTINGS = {"shutdown", "spanning-tree portfast", "ptp enable"}
# single-valued on the box but additive in CLI: replacing needs "no <old>" first
REPLACE_WITH_NO = {"ntp server", "logging host"}

_TOP = sorted(TOP_SETTINGS, key=len, reverse=True)
_SECTION = sorted(SECTION_SETTINGS, key=len, reverse=True)


def split_key(key: str) -> tuple[str | None, str]:
    section, sep, setting = key.partition("|")
    return (section, setting) if sep else (None, key)


def _line(setting: str, value: Any) -> str:
    if value is True:
        return setting
    if value is False:
        return f"no {setting}"
    return f"{setting} {value}"


def _negate(setting: str, value: Any) -> str:
    if setting in FLAG_SETTINGS:
        return _line(setting, not bool(value)) if isinstance(value, bool) else f"no {setting}"
    if value is True or value is False:
        return f"no {setting}"
    return f"no {setting} {value}"


def _emit(grouped: dict[str | None, list[str]]) -> list[str]:
    out = list(grouped.get(None, []))
    for section, lines in grouped.items():
        if section is None or not lines:
            continue
        out.append(section)
        out.extend(f" {ln}" for ln in lines)
    return out


def render(config: dict[str, Any]) -> list[str]:
    grouped: dict[str | None, list[str]] = {None: []}
    for key, value in config.items():
        section, setting = split_key(key)
        grouped.setdefault(section, []).append(_line(setting, value))
    return _emit(grouped)


def remediation(missing: dict[str, Any], changed: dict[str, dict[str, Any]],
                unexpected: dict[str, Any]) -> list[str]:
    """CLI lines that turn observed into desired."""
    grouped: dict[str | None, list[str]] = {None: []}
    for key, info in changed.items():
        section, setting = split_key(key)
        if setting in REPLACE_WITH_NO and info.get("observed") not in (None, True, False):
            grouped.setdefault(section, []).append(f"no {setting} {info['observed']}")
        grouped.setdefault(section, []).append(_line(setting, info["desired"]))
    for key, value in missing.items():
        section, setting = split_key(key)
        grouped.setdefault(section, []).append(_line(setting, value))
    for key, value in unexpected.items():
        section, setting = split_key(key)
        grouped.setdefault(section, []).append(_negate(setting, value))
    return _emit(grouped)


def _parse_setting(text: str, known: list[str]) -> tuple[str, Any, bool]:
    """-> (setting, value, negated)"""
    negated = text.startswith("no ")
    body = text[3:] if negated else text
    for setting in known:
        if body == setting:
            return setting, True, negated
        if body.startswith(setting + " "):
            return setting, body[len(setting) + 1:], negated
    return body, True, negated


def apply_lines(config: dict[str, Any], lines: Iterable[str]) -> dict[str, Any]:
    """What a device does with config lines. Returns a new dict."""
    out = dict(config)
    section: str | None = None
    for raw in lines:
        if not raw.strip() or raw.strip() == "!":
            continue
        indented = raw.startswith(" ")
        text = raw.strip()
        if not indented:
            if text.startswith(SECTION_HEADS):
                section = text
                continue
            section = None
        known = _SECTION if (indented and section) else _TOP
        setting, value, negated = _parse_setting(text, known)
        key = f"{section}|{setting}" if (indented and section) else setting
        if negated:
            if setting in FLAG_SETTINGS:
                out[key] = False
            elif value is True or out.get(key) == value:
                out.pop(key, None)
        else:
            out[key] = value
    return out


def touched_keys(lines: Iterable[str]) -> set[str]:
    """Keys a set of lines would set or remove."""
    probe: dict[str, Any] = {}
    after = apply_lines(probe, lines)
    keys = set(after)
    section: str | None = None
    for raw in lines:                                    # removals don't show in `after`
        text = raw.strip()
        if not text:
            continue
        if not raw.startswith(" "):
            section = text if text.startswith(SECTION_HEADS) else None
            if section:
                continue
        if text.startswith("no "):
            known = _SECTION if (raw.startswith(" ") and section) else _TOP
            setting, _, _ = _parse_setting(text, known)
            keys.add(f"{section}|{setting}" if (raw.startswith(" ") and section) else setting)
    return keys


def rollback(current: dict[str, Any], lines: list[str]) -> list[str]:
    """Lines that undo `lines` on a device whose config is `current`."""
    after = apply_lines(current, lines)
    restore: dict[str, Any] = {}
    remove: dict[str, Any] = {}
    changed: dict[str, dict[str, Any]] = {}
    for key in touched_keys(lines):
        if key in current:
            if after.get(key) != current[key]:
                if key in after:
                    changed[key] = {"desired": current[key], "observed": after[key]}
                else:
                    restore[key] = current[key]
        elif key in after:
            remove[key] = after[key]
    return remediation(restore, changed, remove)
