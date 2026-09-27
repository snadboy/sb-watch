"""Pure rule tests — plain python3, no HA."""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("sb_watch_pure")
_pkg.__path__ = [str(ROOT / "custom_components" / "sb_watch")]
sys.modules["sb_watch_pure"] = _pkg
rule = importlib.import_module("sb_watch_pure.rule")

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def t(secs):
    return T0 + timedelta(seconds=secs)


def test_build_filter_fields_and_yaml_override():
    cfg = rule.build_filter({"patterns": "fp300, light.", "labels": ["matter_hub", "___no_items_available___"], "areas": [],
                             "device_classes": "Battery", "units": "%", "states": "unavailable, <20", "state_for": " 2h "})
    assert cfg == {"patterns": ["fp300", "light."], "labels": ["matter_hub"], "device_classes": ["Battery"], "units": ["%"],
                   "states": ["unavailable", "<20"], "state_for": "2h"}
    cfg = rule.build_filter({"patterns": "fp300", "filter_yaml": "patterns: [sun.]\nstates: [below_horizon]\nstate_for: ''"})
    assert cfg == {"patterns": ["sun."], "states": ["below_horizon"]}
    # YAML 1.1 booleans stay the state strings people mean
    cfg = rule.build_filter({"filter_yaml": "patterns: [light.]\nstates: [on, Off, yes, no]\nstate_min: 20"})
    assert cfg == {"patterns": ["light."], "states": ["on", "Off", "yes", "no"], "state_min": 20}
    try:
        rule.build_filter({"filter_yaml": "- not\n- a mapping"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_no_dwell_baseline_then_changes():
    s = rule.RuleState(dwell_seconds=0)
    assert s.apply(["b", "a"], t(0)) == ((), ())          # first evaluation is a baseline, never an "enter"
    assert s.active == ("a", "b")
    assert s.active_since == t(0)
    assert s.apply(["a", "c"], t(5)) == (("c",), ("b",))
    assert s.apply([], t(9)) == ((), ("a", "c"))
    assert s.active_since is None
    assert s.apply(["z"], t(10)) == (("z",), ())
    assert s.active_since == t(10)


def test_dwell_promotes_after_continuous_match():
    s = rule.RuleState(dwell_seconds=600)
    assert s.apply(["x"], t(0)) == ((), ())
    assert s.active == ()                                   # matched, not yet active
    assert s.next_promotion(t(0)) == 600
    assert s.apply(["x"], t(300)) == ((), ())
    assert s.next_promotion(t(300)) == 300
    assert s.apply(["x"], t(600)) == (("x",), ())            # promoted exactly at the dwell
    assert s.next_promotion(t(600)) is None
    # dropping out resets the clock
    assert s.apply([], t(700)) == ((), ("x",))
    assert s.apply(["x"], t(701)) == ((), ())
    assert s.next_promotion(t(701)) == 600


def test_flap_never_promotes():
    s = rule.RuleState(dwell_seconds=60)
    s.apply(["x"], t(0))
    for k in range(1, 10):
        s.apply([], t(k * 50))
        s.apply(["x"], t(k * 50 + 1))
    assert s.active == ()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("  ok  ", name)
    print("rule tests pass")
