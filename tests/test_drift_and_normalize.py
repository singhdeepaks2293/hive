from datetime import datetime, timezone

from netskills import normalize
from queen.drift import compare
from queen.models import DesiredState, ObservedState

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_compare_missing_changed_and_strict_extras():
    d = DesiredState(device="s", config={"ntp server": "1", "interface G1|description": "up"},
                     source="git:x@1", version="1", published_by="i", published_at=NOW,
                     strict=True)
    o = ObservedState(device="s", config={"ntp server": "9", "logging host": "8"},
                      collected_by="c", collected_at=NOW)
    r = compare(d, o)
    assert r.missing == {"interface G1|description": "up"}
    assert r.changed == {"ntp server": {"desired": "1", "observed": "9"}}
    assert r.unexpected == {"logging host": "8"}


def test_remediation_applied_converges_and_rollback_restores():
    current = {"hostname": "s", "ntp server": "10.0.0.9", "interface G1|shutdown": True}
    lines = normalize.remediation({"interface G1|description": "ring"},
                                  {"ntp server": {"desired": "10.0.0.1",
                                                  "observed": "10.0.0.9"}},
                                  {"interface G1|shutdown": True})
    assert lines[:2] == ["no ntp server 10.0.0.9", "ntp server 10.0.0.1"]
    after = normalize.apply_lines(current, lines)
    assert after["ntp server"] == "10.0.0.1" and after["interface G1|shutdown"] is False
    assert normalize.apply_lines(after, normalize.rollback(current, lines)) == current


def test_render_parse_round_trip():
    cfg = {"hostname": "s", "vlan 30|name": "cameras", "interface G1|rep segment": "1 edge",
           "spanning-tree mode": "rapid-pvst", "service timestamps log datetime msec": True}
    assert normalize.apply_lines({}, normalize.render(cfg)) == cfg
