"""One-time port (2026-10-02): SB Filter grammar 4's state vectors → tests/conditions.json.

Run against the grammar-4 checkout of sb-filter. For every vector whose config has
state keys AND a selection, the selection half is evaluated by SB Filter's matcher
(the ids SB Watch would receive) and stored with the state half and the old
expectations — so SB Watch's condition module must reproduce grammar 4 exactly.
"""
import importlib, json, sys, types
from datetime import datetime
from pathlib import Path

SBF = Path(sys.argv[1] if len(sys.argv) > 1 else "../sb-filter")
_pkg = types.ModuleType("sbf"); _pkg.__path__ = [str(SBF / "custom_components" / "sb_filter")]; sys.modules["sbf"] = _pkg
g = importlib.import_module("sbf.grammar"); m = importlib.import_module("sbf.matcher")
V = json.loads((SBF / "tests" / "vectors.json").read_text())
assert V["grammar"] == 4
s = V["snapshot"]
snap = m.Snapshot(
    states={k: m.StateRow(state=v["state"], attributes=v["attributes"], last_changed=datetime.fromisoformat(v["last_changed"])) for k, v in s["states"].items()},
    entities={k: m.EntityRow(device_id=v.get("device_id"), area_id=v.get("area_id"), labels=tuple(v.get("labels") or [])) for k, v in s["entities"].items()},
    devices={k: m.DeviceRow(area_id=v.get("area_id"), labels=tuple(v.get("labels") or [])) for k, v in s["devices"].items()},
)
COND = ("states", "state_min", "state_max", "state_for", "rate", "rate_window")
cases = []
for c in V["cases"]:
    cond = {k: v for k, v in c["config"].items() if k in COND}
    sel = {k: v for k, v in c["config"].items() if k not in COND}
    if not cond or not g.parse_filter(sel).configured:
        continue                                     # nothing to port: selection-only, or a condition with no selection
    ids = list(m.evaluate(g.parse_filter(sel), snap, datetime.fromisoformat(s["now"])).ids)
    out = {"name": c["name"], "selected": ids, "condition": cond, "expect_ids": c["expect_ids"]}
    for k in ("unreadable", "unmatched_values"):
        if k in c:
            out[k] = c[k]
    cases.append(out)
snapshot = {k: s[k] for k in ("now", "states", "formatted", "vocabulary", "history") if k in s}
Path("tests/conditions.json").write_text(json.dumps({"ported_from": "sb_filter grammar 4", "snapshot": snapshot, "cases": cases}, indent=1, ensure_ascii=False) + "\n")
print(len(cases), "cases")
