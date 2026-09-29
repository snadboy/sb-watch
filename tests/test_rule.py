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


def test_no_dwell_first_evaluation_enters():
    s = rule.RuleState(dwell_seconds=0)
    assert s.apply(["b", "a"], t(0)) == (("a", "b"), ())  # a brand-new rule: what it finds has just entered
    assert s.active == ("a", "b")
    assert s.active_since == t(0)
    assert s.apply(["a", "c"], t(5)) == (("c",), ("b",))
    assert s.apply([], t(9)) == ((), ("a", "c"))
    assert s.active_since is None
    assert s.apply(["z"], t(10)) == (("z",), ())
    assert s.active_since == t(10)


def test_restored_rule_does_not_reenter_unchanged_set():
    s = rule.RuleState(dwell_seconds=0)
    s.active = ("a", "b"); s.matched_since = {"a": t(-100), "b": t(-100)}; s.baselined = True   # as restore() leaves it
    assert s.apply(["a", "b"], t(0)) == ((), ())
    assert s.apply(["a", "b", "c"], t(1)) == (("c",), ())


def test_dwell_promotes_after_continuous_match():
    s = rule.RuleState(dwell_seconds=600)
    assert s.apply(["x"], t(0)) == ((), ())                 # nothing active yet: still dwelling
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


def test_format_message():
    import importlib
    fm = importlib.import_module("sb_watch_pure.actions_pure").format_message
    assert fm([], 0) == "Clear"
    assert fm(["A", "B"], 2) == "2 active: A, B"
    names = [f"N{i}" for i in range(12)]
    assert fm(names, 12).endswith("N7 and 4 more")
    assert fm(["A"], 1, "turn_off", 600) == "1 active: A — turn off in 10 min unless cleared"
    assert fm(["A"], 1, "turn_off", 30) == "1 active: A — turn off in 30 s unless cleared"


def test_effect_window_and_days():
    E = rule.Effect
    def at(dow, h, m=0):   # 2026-09-28 is a Monday
        return datetime(2026, 9, 28, h, m, tzinfo=timezone.utc) + timedelta(days=dow)
    assert E.from_options({}).always
    e = E.from_options({"window_enabled": True, "window_start": "09:00:00", "window_end": "17:00:00"})
    assert e.in_effect(at(0, 9)) and e.in_effect(at(0, 16, 59)) and not e.in_effect(at(0, 17)) and not e.in_effect(at(0, 8, 59))
    n = E.from_options({"window_enabled": True, "window_start": "18:00", "window_end": "06:00"})      # crosses midnight
    assert n.in_effect(at(0, 18)) and n.in_effect(at(0, 23, 59)) and n.in_effect(at(1, 0)) and n.in_effect(at(1, 5, 59))
    assert not n.in_effect(at(1, 6)) and not n.in_effect(at(0, 12))
    # days: Friday night's window belongs to Friday, so Saturday 03:00 is "Friday"
    fri = E.from_options({"window_enabled": True, "window_start": "18:00", "window_end": "06:00", "days_enabled": True, "days": ["fri"]})
    assert fri.in_effect(at(4, 20)) and fri.in_effect(at(5, 3)) and not fri.in_effect(at(5, 20)) and not fri.in_effect(at(3, 20))
    wk = E.from_options({"days_enabled": True, "days": ["sat", "sun"]})
    assert wk.in_effect(at(5, 12)) and not wk.in_effect(at(0, 12))
    both = E.from_options({"window_enabled": True, "window_start": "09:00", "window_end": "17:00", "days_enabled": True, "days": ["mon"]})
    assert both.in_effect(at(0, 10)) and not both.in_effect(at(0, 18)) and not both.in_effect(at(1, 10))
    # unreadable / empty window = no window; days enabled with none ticked = every day
    assert E.from_options({"window_enabled": True, "window_start": "x", "window_end": "06:00"}).start is None
    assert E.from_options({"days_enabled": True, "days": []}).in_effect(at(2, 12))
    # next change: same-day window at 10:00 → 17:00 today; crossing window at 20:00 → 06:00 tomorrow; days → midnight
    assert e.next_change(at(0, 10)) == at(0, 17) + timedelta(seconds=1)
    assert n.next_change(at(0, 20)) == at(1, 6) + timedelta(seconds=1)
    assert wk.next_change(at(0, 12)) == at(1, 0) + timedelta(seconds=1)
    assert E.from_options({}).next_change(at(0, 12)) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("  ok  ", name)
    print("rule tests pass")
