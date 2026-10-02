"""State conditions — the vectors SB Filter carried up to grammar 4, ported (tools/port_vectors.py).

Each case: the ids SB Filter's selection hands over, the state half of the old
filter, and the old expectations. Plain python3, no HA.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_pkg = types.ModuleType("sb_watch_pure")
_pkg.__path__ = [str(ROOT / "custom_components" / "sb_watch")]
sys.modules["sb_watch_pure"] = _pkg
cond = importlib.import_module("sb_watch_pure.condition")

V = json.loads((ROOT / "tests" / "conditions.json").read_text())


def _world():
    s = V["snapshot"]
    rows = {k: cond.StateRow(state=v["state"], attributes=v["attributes"], last_changed=datetime.fromisoformat(v["last_changed"]))
            for k, v in s["states"].items()}
    look = cond.Lookup(
        formatted=lambda e: s["formatted"].get(e),
        vocabulary=lambda e: [tuple(p) for p in s.get("vocabulary", {}).get(e, [])],
        history=lambda e, w: [(datetime.fromisoformat(t), float(v)) for t, v in s.get("history", {}).get(e, [])],
    )
    return rows, look, datetime.fromisoformat(s["now"])


def _run(case):
    rows, look, now = _world()
    c = cond.parse_condition(case["condition"])
    got = cond.matching(c, rows, case["selected"], look, now)
    assert got == case["expect_ids"], f"{case['name']}: got {got}"
    if "unreadable" in case:
        assert list(c.unreadable) == case["unreadable"], f"{case['name']}: unreadable={list(c.unreadable)}"
    if "unmatched_values" in case:
        um = [{"value": u.value, "suggestions": list(u.suggestions)} for u in cond.unmatched_values(c, rows, case["selected"], look)]
        assert um == case["unmatched_values"], f"{case['name']}: unmatched={um}"


def test_ported_vectors():
    for case in V["cases"]:
        _run(case)


def test_values_counts():
    rows, look, _ = _world()
    occ = [e for e in rows if e.endswith("fp300_occupancy")]
    vals = {v["value"]: v for v in cond.values(rows, occ, look)}
    assert vals["off"]["current"] == 1 and vals["on"]["current"] == 1


def test_empty_condition_holds():
    rows, look, now = _world()
    assert cond.matching(cond.parse_condition({}), rows, ["light.kitchen", "nope.x"], look, now) == ["light.kitchen"]


if __name__ == "__main__":
    failed = 0
    for case in V["cases"]:
        try:
            _run(case)
        except AssertionError as e:
            failed += 1
            print("  FAIL", e)
    test_values_counts()
    test_empty_condition_holds()
    print(f"{len(V['cases']) - failed}/{len(V['cases'])} ported vectors pass")
    sys.exit(1 if failed else 0)
