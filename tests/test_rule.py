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


# ---- the model: selection + triggers (0.9.0) ---------------------------------
LIVE_V1 = {   # the nine rules as they were stored on 2026-10-01 (filter keys only)
    "Batteries low": {"device_classes": "battery", "units": "%", "states": ["unavailable", "<20"], "state_min": "asdf", "state_max": "qwer", "for": "2m", "problem": True},
    "Nest hubs off": {"labels": ["matter_hub"], "states": ["off"], "problem": True},
    "Lights on too long": {"patterns": "light.", "states": ["on"], "state_for": "4h", "filter_yaml": "patterns: [light.]\nstates: [on]\nstate_for: 4h", "problem": True},
    "Indoor temperature": {"patterns": "basement fp300_temperature, main_floor fp300_temperature, upstairs fp300_temperature", "units": "°F", "states": ["<60", ">82"], "for": "15m", "problem": True},
    "Garage Door Open timeout": {"patterns": "cover.ratgdov25i_4b1b1b_door", "states": ["open"], "state_for": "115m", "problem": True, "window_enabled": True, "window_start": "19:00:00", "window_end": "05:00:00", "action": "notify_then_act"},
}


def test_durations_and_ranges():
    assert rule.duration_seconds("2h") == 7200 and rule.duration_seconds("1h30m") == 5400 and rule.duration_seconds("90s") == 90
    assert rule.duration_seconds("15") == 900 and rule.duration_seconds("1d") == 86400
    assert rule.duration_seconds("") is None and rule.duration_seconds("<5m") is None and rule.duration_seconds("soon") is None
    assert [rule.duration_text(s) for s in (7200, 5400, 90, 172800, 6900, 120)] == ["2h", "90m", "90s", "2d", "115m", "2m"]
    for v in ("<20", ">=80", "15-50", "15..50", "=3", "100", "-5"):
        assert rule.is_range(v), v
    for v in ("off", "unavailable", "", "one hundred", "<x"):
        assert not rule.is_range(v), v


def test_upgrade_live_rules():
    up = {k: rule.upgrade_options(v) for k, v in LIVE_V1.items()}
    b = up["Batteries low"]
    assert b["classes"] == ["battery:%"] and b["patterns"] == [] and b["filter_yaml"] == "" and b["for"] == ""
    assert b["triggers"] == [{"kind": "state", "value": "unavailable", "for": "2m"}, {"kind": "range", "value": "<20", "for": "2m"}]
    assert not any(k in b for k in rule.LEGACY_KEYS), "legacy keys (and the junk state_min) are gone"
    assert up["Nest hubs off"]["labels"] == ["matter_hub"] and up["Nest hubs off"]["triggers"] == [{"kind": "state", "value": "off", "for": ""}]
    # a YAML override that only repeated the fields is absorbed into the model
    l = up["Lights on too long"]
    assert l["patterns"] == ["light."] and l["triggers"] == [{"kind": "state", "value": "on", "for": "4h"}] and l["filter_yaml"] == ""
    t = up["Indoor temperature"]
    assert t["classes"] == [":°F"] and len(t["patterns"]) == 3
    assert t["triggers"] == [{"kind": "range", "value": "<60", "for": "15m"}, {"kind": "range", "value": ">82", "for": "15m"}]
    g = up["Garage Door Open timeout"]
    assert g["triggers"] == [{"kind": "state", "value": "open", "for": "115m"}]
    assert g["window_enabled"] is True and g["action"] == "notify_then_act", "everything else rides along untouched"
    assert rule.upgrade_options(g) is g, "already upgraded: unchanged"


def test_upgrade_keeps_what_the_model_cannot_say_as_yaml():
    for v1 in ({"patterns": "sensor.", "state_for": "<10m"},                       # changed RECENTLY
               {"patterns": "sensor.", "states": ["on"], "rate": ">0.5/h"},        # rate ANDed with a state
               {"patterns": "sensor.", "for": "10m"}):                             # a dwell with nothing to time
        up = rule.upgrade_options(v1)
        assert up["triggers"] == [] and up["filter_yaml"].strip(), v1
        clauses = rule.build_clauses(up)
        assert len(clauses) == 1 and clauses[0].key == "yaml"
        sel, cnd = rule.split_config(rule.build_filter(v1))
        assert rule.rule_selection(up) == sel and clauses[0].condition == cnd, "the YAML clause is the v1 filter, exactly, split in two"
        assert clauses[0].dwell_seconds == (rule.duration_seconds(v1.get("for")) or 0)
    r = rule.upgrade_options({"patterns": "temp", "rate": ">0.5/h, <-2/h", "rate_window": "30m"})
    assert r["triggers"] == [{"kind": "rate", "value": ">0.5", "for": "30m", "per": "h"}, {"kind": "rate", "value": "<-2", "for": "30m", "per": "h"}]


def test_build_clauses():
    o = {"patterns": ["fp300", "lwr02"], "labels": ["office"], "areas": [], "classes": ["battery:%"],
         "triggers": [{"kind": "state", "value": "Off", "for": "2h"}, {"kind": "range", "value": "=3", "for": "1m"},
                      {"kind": "range", "value": "15-50", "for": "10m"}, {"kind": "rate", "value": "> 0.5", "per": "h", "for": "5m"},
                      {"kind": "state", "value": "*", "for": "6h"}, {"kind": "state", "value": "", "for": ""}]}
    sel = {"patterns": ["fp300", "lwr02"], "labels": ["office"], "classes": ["battery:%"]}
    assert rule.selection_filter(o) == sel
    cs = rule.build_clauses(o)
    assert rule.rule_selection(o) == sel, "SB Filter gets the selection once, for every clause"
    assert [(c.condition, c.dwell_seconds) for c in cs] == [
        ({"states": ["Off"]}, 7200.0),                                  # timed by the clause's own clock
        ({"states": ["3"]}, 60.0),
        ({"states": ["15-50"]}, 600.0),
        ({"rate": [">0.5/h"], "rate_window": "5m"}, 0.0),
        ({"state_for": "6h"}, 0.0),                                     # any state: only last_changed can time "unchanged for"
    ], "the empty row is dropped"
    assert len({c.key for c in cs}) == len(cs)
    # a number typed into a State row is still timed as a range
    assert rule.build_clauses({"patterns": ["x"], "triggers": [{"kind": "state", "value": "<20", "for": "5m"}]})[0].dwell_seconds == 300
    # no triggers: the selection itself; identical rows get distinct keys
    assert [c.key for c in rule.build_clauses({"patterns": ["x"], "triggers": []})] == ["all"]
    dup = rule.build_clauses({"patterns": ["x"], "triggers": [{"kind": "state", "value": "on", "for": ""}] * 2})
    assert [c.key for c in dup] == ["state|on||", "state|on||#1"]
    assert rule.trigger_errors(rule.clean_triggers([{"kind": "range", "value": "warm", "for": "10m"}, {"kind": "rate", "value": "0.5", "for": "x"}])) != []
    assert rule.trigger_errors(rule.clean_triggers(o["triggers"])) == []


def test_engine_union_and_per_clause_dwell():
    cs = rule.build_clauses({"patterns": ["s"], "triggers": [{"kind": "state", "value": "unavailable", "for": "2m"},
                                                              {"kind": "range", "value": "<20", "for": "10m"}]})
    ka, kb = cs[0].key, cs[1].key
    eng = rule.RuleEngine(cs)
    # a has been unavailable for an hour already (its last_changed): active at once.
    # b's value last changed 30 s ago: 9.5 minutes of its 10 still to run.
    since = {"a": t(-3600), "b": t(-30)}
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(0), since) == (("a",), ())
    assert eng.next_promotion(t(0)) == 570
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(569), since) == ((), ())
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(570), {"a": t(-3600), "b": t(500)}) == (("b",), ()), "a running clock is never moved by a later last_changed"
    eng = rule.RuleEngine(cs)
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(0), {"a": t(-3600)}) == (("a",), ())
    assert eng.next_promotion(t(0)) == 600
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(599)) == ((), ())
    assert eng.apply({ka: ["a"], kb: ["b"]}, t(600)) == (("b",), ()) and eng.active == ("a", "b")
    # b leaves the range for a moment: its clock restarts; a moves from one clause to the other without leaving the RULE
    assert eng.apply({ka: ["a"], kb: []}, t(700)) == ((), ("b",))
    assert eng.apply({ka: [], kb: ["a", "b"]}, t(800)) == ((), ("a",)), "a is pending in the range clause now"
    assert eng.apply({ka: ["a"], kb: ["a", "b"]}, t(900)) == ((), ()), "back in the state clause: its 2 min start over"
    assert eng.apply({ka: ["a"], kb: ["a", "b"]}, t(1020)) == (("a",), ())
    assert eng.apply({ka: [], kb: ["a", "b"]}, t(1400)) == (("b",), ()), "a stays: its range clock (since 800) has run its 10 min"
    assert eng.active == ("a", "b")


def test_engine_restore_new_and_v1_snapshots():
    cs = rule.build_clauses({"patterns": ["s"], "triggers": [{"kind": "range", "value": "<20", "for": "10m"}, {"kind": "range", "value": ">90", "for": "10m"}]})
    eng = rule.RuleEngine(cs)
    eng.apply({cs[0].key: ["a"], cs[1].key: ["b"]}, t(0)); eng.apply({cs[0].key: ["a"], cs[1].key: ["b"]}, t(600))
    snap = eng.snapshot()
    parse = datetime.fromisoformat
    fresh = rule.RuleEngine(cs); fresh.restore(snap, parse)
    assert fresh.active == ("a", "b") and fresh.baselined
    assert fresh.apply({cs[0].key: ["a"], cs[1].key: ["b"]}, t(660)) == ((), ()), "nothing re-enters after a restart"
    # a v1 snapshot (one clock set for the rule) seeds every clause: clocks keep running through the upgrade
    v1 = {"matched_since": {"a": t(0).isoformat(), "c": t(300).isoformat()}, "active": ["a"], "active_since": t(600).isoformat()}
    up = rule.RuleEngine(cs); up.restore(v1, parse)
    assert up.apply({cs[0].key: ["a", "c"], cs[1].key: []}, t(700)) == ((), ()), "a stays active, c is still 200 s short"
    assert up.apply({cs[0].key: ["a", "c"], cs[1].key: []}, t(900)) == (("c",), ())


def test_triggers_text():
    rows = rule.clean_triggers([{"kind": "state", "value": "unavailable", "for": "2m"}, {"kind": "range", "value": "<20", "for": "2m"},
                                {"kind": "rate", "value": ">0.5", "per": "h", "for": "5m"}, {"kind": "state", "value": "", "for": "6h"}, {"kind": "state", "value": "off", "for": ""}])
    assert rule.triggers_text(rows) == "unavailable for 2m · <20 for 2m · >0.5/h over 5m · any state for 6h · off"


def test_split_config():
    sel, cnd = rule.split_config({"patterns": ["x"], "classes": ["battery:%"], "states": ["<20"], "state_for": "1h"})
    assert sel == {"patterns": ["x"], "classes": ["battery:%"]} and cnd == {"states": ["<20"], "state_for": "1h"}
    try:
        rule.split_config({"patterns": ["x"], "sort": "name"})
        raise AssertionError("an unknown key must not be dropped silently")
    except ValueError:
        pass
    assert rule.build_clauses({"patterns": ["x"], "triggers": []})[0].condition == {}, "no triggers: every selected entity counts"


def test_rule_source_and_migration_target():
    assert rule.rule_source({"filter": "abc", "patterns": ["x"]}) == {"filter": "abc"}
    assert rule.rule_source({"entities": ["light.a", "light.a", "junk"]}) == {"entities": ["light.a"]}
    assert rule.rule_source({"patterns": ["light."]}) == {"selection": {"patterns": ["light."]}}, "an unmigrated v2 rule still runs"
    assert rule.rule_source({"patterns": ["x"], "filter_yaml": "labels: [a]\nstates: [on]"}) == {"selection": {"patterns": ["x"], "labels": ["a"]}}
    assert rule.rule_source({}) == {}
    real = {"light.desk_bulb_l535e"}.__contains__
    assert rule.migration_target({"patterns": ["light.desk_bulb_l535e"]}, real) == {"entities": ["light.desk_bulb_l535e"]}
    assert rule.migration_target({"patterns": ["light."]}, real) == {"selection": {"patterns": ["light."]}}
    assert rule.migration_target({"patterns": ["light.desk_bulb_l535e"], "labels": ["x"]}, real) == {"selection": {"patterns": ["light.desk_bulb_l535e"], "labels": ["x"]}}
    assert rule.migration_target({"classes": ["battery:%"]}, real) == {"selection": {"classes": ["battery:%"]}}
    assert rule.migration_target({"patterns": ["x"], "filter_yaml": "labels: [a]"}, real) is None, "the YAML path keeps its inline selection"
    assert rule.migration_target({"filter": "abc"}, real) is None


def test_yaml_conditions_on_a_filter():
    o = {"filter": "abc", "filter_yaml": "state_for: '<10m'", "triggers": []}
    assert rule.rule_source(o) == {"filter": "abc"}, "YAML with only state keys runs on the rule's filter"
    assert rule.build_clauses(o)[0].condition == {"state_for": "<10m"}
    assert rule.rule_source({"filter": "abc", "filter_yaml": "patterns: [x]\nstate_for: '<10m'"}) == {"selection": {"patterns": ["x"]}}, "selection keys in YAML still win"
