"""Rule logic, pure: no Home Assistant imports.

A rule is a SELECTION (which entities: patterns, labels, areas, class:unit
pairs) plus TRIGGERS (what makes one of them count), each trigger with its own
duration. Every trigger becomes a CLAUSE: one SB Filter config (the selection
plus that trigger's condition) and a dwell. An entity is ACTIVE while any
clause holds it.

  state  "off" for 2h      → states: [off],   dwell 2 h
  range  "15-50" for 10m   → states: [15-50], dwell 10 min
  rate   ">0.5" per h over 5m → rate: [">0.5/h"], rate_window: 5m
  state  (any) for 6h      → state_for: 6h    (no value to match on, so only
                             SB Filter's time-since-last-change can time it)

The dwell is a clock per entity per clause: it starts when the entity first
matches, SEEDED from the entity's last_changed (it has held that state at least
since then — so a bulb already on for two hours counts at once when a rule is
created), and it is PERSISTED, so it survives a Home Assistant restart —
which last_changed itself does not.

The advanced YAML override (a pasted card filter the form cannot express) is
one clause with the rule-level dwell `for`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

import yaml


class _FilterLoader(yaml.SafeLoader):
    """YAML 1.1 reads `on`, `off`, `yes`, `no` as booleans — and `on`/`off`
    are the most common states anyone writes. Keep them as strings."""


_FilterLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def split_list(v: Any) -> list[str]:
    """A comma string from a form field → list; a list passes through; blanks dropped."""
    if v is None or v == "":
        return []
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return [str(s).strip() for s in items if s is not None and str(s).strip()]


def build_filter(options: dict[str, Any]) -> dict[str, Any]:
    """The filter config for SB Filter from a rule's options.

    Form fields first; the advanced YAML mapping (if any) overrides key by key,
    so a pasted card config wins over whatever the pickers say.
    """
    cfg: dict[str, Any] = {}
    for key in ("patterns", "device_classes", "units", "states"):
        lst = split_list(options.get(key))
        if lst:
            cfg[key] = lst
    for key in ("labels", "areas"):
        lst = [s for s in (options.get(key) or []) if s and not str(s).startswith("___")]
        if lst:
            cfg[key] = lst
    if options.get("state_for"):
        cfg["state_for"] = str(options["state_for"]).strip()
    rate = split_list(options.get("rate"))
    if rate:
        cfg["rate"] = rate
    if options.get("rate_window"):
        cfg["rate_window"] = str(options["rate_window"]).strip()
    raw = options.get("filter_yaml")
    if raw and str(raw).strip():
        parsed = yaml.load(str(raw), Loader=_FilterLoader)
        if not isinstance(parsed, dict):
            raise ValueError("filter_yaml must be a YAML mapping")
        for k, v in parsed.items():
            if v is None or v == "" or v == []:
                cfg.pop(k, None)
            else:
                cfg[k] = v
    return cfg


# ---- durations and value shapes ----------------------------------------------
_DUR_PART = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])", re.I)
_DUR_FULL = re.compile(r"(?:\d+(?:\.\d+)?\s*[dhms]\s*)+", re.I)
_DUR_UNIT = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}
_NUM = r"-?\d+(?:\.\d+)?"
_RANGE_RX = re.compile(rf"^\s*(?:(?:<=|>=|<|>|=)\s*{_NUM}|{_NUM}\s*(?:-|\.\.)\s*{_NUM}|{_NUM})\s*$")
_RATE_RX = re.compile(rf"^\s*(<=|>=|<|>)\s*({_NUM})\s*$")
_RATE_TERM_RX = re.compile(rf"^(<=|>=|<|>)({_NUM})/([mhd])$")
KINDS = ("state", "range", "rate")
PERS = ("m", "h", "d")


def duration_seconds(v: Any) -> float | None:
    """'2h', '1h30m', '90s', '1d'; a bare number is MINUTES. No comparator: a trigger's duration means 'at least'."""
    s = str(v if v is not None else "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s) * 60.0
    if not _DUR_FULL.fullmatch(s):
        return None
    return sum(float(n) * _DUR_UNIT[u.lower()] for n, u in _DUR_PART.findall(s))


def duration_text(seconds: float) -> str:
    """7200 → '2h', 5400 → '90m', 90 → '90s', 172800 → '2d'."""
    secs = int(round(seconds))
    if secs % 60:
        return f"{secs}s"
    m = secs // 60
    if m and m % 1440 == 0:
        return f"{m // 1440}d"
    if m and m % 60 == 0:
        return f"{m // 60}h"
    return f"{m}m"


def is_range(v: Any) -> bool:
    """A numeric condition: <20, >=80, 15-50, 15..50, =3 or a plain number."""
    return bool(_RANGE_RX.match(str(v if v is not None else "")))


def _norm_range(v: Any) -> str:
    s = str(v).strip()
    return s[1:].strip() if s.startswith("=") else s


# ---- the model: selection + triggers -----------------------------------------
def clean_triggers(raw: Any) -> list[dict[str, str]]:
    """Normalise trigger rows; rows with neither a value nor a duration are dropped."""
    out: list[dict[str, str]] = []
    for t in raw or []:
        if not isinstance(t, dict):
            continue
        value = str(t.get("value") if t.get("value") is not None else "").strip()
        dur = str(t.get("for") if t.get("for") is not None else "").strip()
        kind = str(t.get("kind") or "").strip().lower()
        if kind not in KINDS:
            kind = "range" if is_range(value) else "state"
        if kind == "state" and value == "*":
            value = ""                                   # "*" = any state
        if kind == "state" and is_range(value):
            kind = "range"                               # a number can only be timed as a range (see module doc)
        if not value and not dur:
            continue
        row = {"kind": kind, "value": value, "for": dur}
        if kind == "rate":
            per = str(t.get("per") or "h").strip().lower()[:1]
            row["per"] = per if per in PERS else "h"
        out.append(row)
    return out


def trigger_errors(triggers: list[dict[str, str]]) -> list[str]:
    """Human-readable problems with trigger rows (empty = fine)."""
    errs: list[str] = []
    for t in triggers:
        label = t["value"] or "any state"
        if t["for"] and duration_seconds(t["for"]) is None:
            errs.append(f"{label}: '{t['for']}' is not a duration (2h, 10m, 90s)")
        if t["kind"] == "range" and not is_range(t["value"]):
            errs.append(f"'{t['value']}' is not a range (<20, >=80, 15-50, =3)")
        if t["kind"] == "rate" and not _RATE_RX.match(t["value"]):
            errs.append(f"'{t['value']}' is not a rate (>0.5, <=-2)")
        if t["kind"] == "state" and not t["value"] and not t["for"]:
            errs.append("a state trigger needs a state, a duration, or both")
    return errs


def selection_filter(options: dict[str, Any]) -> dict[str, Any]:
    """The 'which entities' half as an SB Filter config."""
    cfg: dict[str, Any] = {}
    pats = split_list(options.get("patterns"))
    if pats:
        cfg["patterns"] = pats
    for key in ("labels", "areas"):
        lst = [s for s in (options.get(key) or []) if s and not str(s).startswith("___")]
        if lst:
            cfg[key] = lst
    classes = [c for c in split_list(options.get("classes")) if c.strip(": ")]
    if classes:
        cfg["classes"] = classes
    return cfg


@dataclass(frozen=True)
class Clause:
    key: str                       # stable across restarts: derived from the trigger's content
    filter: dict[str, Any]
    dwell_seconds: float = 0.0
    trigger: dict[str, str] | None = None


def _yaml_filter(options: dict[str, Any]) -> dict[str, Any]:
    """Selection fields with the advanced YAML mapping laid over them, key by key."""
    cfg = selection_filter(options)
    parsed = yaml.load(str(options.get("filter_yaml")), Loader=_FilterLoader)
    if not isinstance(parsed, dict):
        raise ValueError("filter_yaml must be a YAML mapping")
    for k, v in parsed.items():
        if v is None or v == "" or v == []:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    return cfg


def build_clauses(options: dict[str, Any]) -> list[Clause]:
    """One clause per trigger; the YAML override or a trigger-less rule is a single clause."""
    if str(options.get("filter_yaml") or "").strip():
        return [Clause(key="yaml", filter=_yaml_filter(options), dwell_seconds=duration_seconds(options.get("for")) or 0.0)]
    sel = selection_filter(options)
    triggers = clean_triggers(options.get("triggers"))
    if not triggers:
        return [Clause(key="all", filter=sel)]
    clauses: list[Clause] = []
    seen: dict[str, int] = {}
    for t in triggers:
        f = dict(sel)
        dwell = 0.0
        if t["kind"] == "state" and t["value"]:
            f["states"] = [t["value"]]
            dwell = duration_seconds(t["for"]) or 0.0
        elif t["kind"] == "state":                   # any state: only time-since-last-change can say "unchanged for"
            f["state_for"] = t["for"]
        elif t["kind"] == "range":
            f["states"] = [_norm_range(t["value"])]
            dwell = duration_seconds(t["for"]) or 0.0
        else:
            f["rate"] = [f"{t['value'].replace(' ', '')}/{t.get('per') or 'h'}"]
            if t["for"]:
                f["rate_window"] = t["for"]
        key = "|".join((t["kind"], t["value"], t["for"], t.get("per", "")))
        n = seen.get(key, 0)
        seen[key] = n + 1
        clauses.append(Clause(key=key if n == 0 else f"{key}#{n}", filter=f, dwell_seconds=dwell, trigger=t))
    return clauses


# ---- upgrading v1 options (one filter + one dwell) to the model ---------------
_FILTER_KEYS = {"patterns", "labels", "areas", "device_classes", "units", "classes", "states", "state_min", "state_max",
                "state_for", "rate", "rate_window"}
LEGACY_KEYS = ("device_classes", "units", "states", "state_min", "state_max", "state_for", "rate", "rate_window")


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def model_from_filter(cfg: dict[str, Any], dwell_text: Any = None) -> dict[str, Any] | None:
    """A v1 filter + rule dwell as selection + triggers, or None when the model cannot say it
    (a `<5m` time-in-state, a rate ANDed with states, a dwell with nothing to time)."""
    if set(cfg) - _FILTER_KEYS:
        return None
    dcs, units, classes = split_list(cfg.get("device_classes")), split_list(cfg.get("units")), split_list(cfg.get("classes"))
    if classes and (dcs or units):
        return None                       # two ANDed categories; pairs are one
    if dcs and units:
        classes = [f"{d}:{u}" for d in dcs for u in units]
    elif dcs:
        classes = list(dcs)
    elif units:
        classes = [f":{u}" for u in units]
    states = [str(v) for v in split_list(cfg.get("states"))]
    lo, hi = _num(cfg.get("state_min")), _num(cfg.get("state_max"))
    if lo is not None and hi is not None:
        states.append(f"{lo:g}..{hi:g}")
    elif lo is not None:
        states.append(f">={lo:g}")
    elif hi is not None:
        states.append(f"<={hi:g}")
    sf = str(cfg.get("state_for") if cfg.get("state_for") is not None else "").strip()
    if sf.startswith(">="):
        sf = sf[2:].strip()
    sf_secs = 0.0
    if sf:
        parsed = duration_seconds(sf)
        if parsed is None:
            return None
        sf_secs = parsed
    dwell = duration_seconds(dwell_text) or 0.0
    rates = [str(r).replace(" ", "") for r in split_list(cfg.get("rate"))]
    total = sf_secs + dwell               # v1: in the state for `state_for`, THEN matched for the dwell
    dur = duration_text(total) if total > 0 else ""
    triggers: list[dict[str, str]] = []
    if rates:
        if states or sf or dwell:
            return None
        window = str(cfg.get("rate_window") or "").strip()
        for r in rates:
            m = _RATE_TERM_RX.match(r)
            if not m:
                return None
            triggers.append({"kind": "rate", "value": f"{m.group(1)}{m.group(2)}", "for": window, "per": m.group(3)})
    elif states:
        triggers = [{"kind": "range" if is_range(v) else "state", "value": v, "for": dur} for v in states]
    elif sf:
        triggers = [{"kind": "state", "value": "", "for": dur}]
    elif dwell:
        return None
    return {
        "patterns": split_list(cfg.get("patterns")),
        "labels": [s for s in split_list(cfg.get("labels")) if not s.startswith("___")],
        "areas": [s for s in split_list(cfg.get("areas")) if not s.startswith("___")],
        "classes": classes,
        "triggers": triggers,
    }


def upgrade_options(options: dict[str, Any]) -> dict[str, Any]:
    """v1 options → the model. Already-upgraded options pass through unchanged."""
    if "triggers" in options:
        return options
    out = {k: v for k, v in options.items() if k not in LEGACY_KEYS}
    try:
        cfg = build_filter(options)
        model = model_from_filter(cfg, options.get("for"))
    except Exception:  # noqa: BLE001 — unreadable YAML: keep it as written for the user to fix
        cfg, model = None, None
    if model is not None:
        out.update(model)
        out["filter_yaml"] = ""
        out["for"] = ""
        return out
    dcs, units = split_list(options.get("device_classes")), split_list(options.get("units"))
    out.update({
        "patterns": split_list(options.get("patterns")),
        "labels": [s for s in (options.get("labels") or []) if s and not str(s).startswith("___")],
        "areas": [s for s in (options.get("areas") or []) if s and not str(s).startswith("___")],
        "classes": [f"{d}:{u}" for d in dcs for u in units] if dcs and units else (list(dcs) or [f":{u}" for u in units]),
        "triggers": [],
        "filter_yaml": yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True) if cfg is not None else str(options.get("filter_yaml") or ""),
        "for": str(options.get("for") or ""),
    })
    return out


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hm(v: Any) -> tuple[int, int] | None:
    """'18:00' / '18:00:00' → (18, 0)."""
    if v is None or v == "":
        return None
    parts = str(v).strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


@dataclass(frozen=True)
class Effect:
    """When a rule is in effect: an optional daily window (may cross midnight)
    AND an optional set of weekdays. Both absent = always."""

    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    days: frozenset[str] = frozenset()

    @classmethod
    def from_options(cls, o: dict[str, Any]) -> "Effect":
        start = end = None
        if o.get("window_enabled"):
            start, end = _hm(o.get("window_start")), _hm(o.get("window_end"))
            if start is None or end is None or start == end:
                start = end = None          # unreadable or empty window: no window
        days = frozenset(d for d in (o.get("days") or []) if d in WEEKDAYS) if o.get("days_enabled") else frozenset()
        if o.get("days_enabled") and not days:
            days = frozenset(WEEKDAYS)      # "on these days" with none ticked would mean never; treat as every day
        return cls(start=start, end=end, days=days)

    @property
    def always(self) -> bool:
        return self.start is None and not self.days

    def _in_window(self, local: datetime) -> tuple[bool, int]:
        """(inside?, day offset of the window's start: 0 today, -1 yesterday)."""
        if self.start is None:
            return True, 0
        now = (local.hour, local.minute)
        if self.start < self.end:                       # same day, e.g. 09:00-17:00
            return self.start <= now < self.end, 0
        # crosses midnight, e.g. 18:00-06:00
        if now >= self.start:
            return True, 0
        if now < self.end:
            return True, -1
        return False, 0

    def in_effect(self, local: datetime) -> bool:
        inside, offset = self._in_window(local)
        if not inside:
            return False
        if self.days:
            day = WEEKDAYS[(local.weekday() + offset) % 7]
            if day not in self.days:
                return False
        return True

    def next_change(self, local: datetime) -> datetime | None:
        """The next local time the answer could change (a window edge or midnight), or None if always."""
        if self.always:
            return None
        candidates = []
        base = local.replace(second=0, microsecond=0)
        for hm in (self.start, self.end):
            if hm is None:
                continue
            t = base.replace(hour=hm[0], minute=hm[1])
            if t <= local:
                t += timedelta(days=1)
            candidates.append(t)
        if self.days:
            candidates.append((base + timedelta(days=1)).replace(hour=0, minute=0))
        return min(candidates) + timedelta(seconds=1)


@dataclass
class RuleState:
    """Which matched entities have dwelt long enough to be active."""

    dwell_seconds: float = 0.0
    matched_since: dict[str, datetime] = field(default_factory=dict)
    active: tuple[str, ...] = ()
    active_since: datetime | None = None    # when the active set last became non-empty
    baselined: bool = False                 # True once evaluated or restored (restore() pre-seeds active)

    def apply(self, matched: list[str] | tuple[str, ...], now: datetime,
              since: dict[str, datetime] | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Feed the current match set; returns (entered, left) relative to the previous ACTIVE set.

        `since` (entity → its last_changed) seeds the clock of an entity matching for the
        first time: it has been in that state at least that long. A clock already running
        (or restored) is never moved."""
        matched_set = set(matched)
        for entity_id in list(self.matched_since):
            if entity_id not in matched_set:
                del self.matched_since[entity_id]
        for entity_id in matched_set:
            seed = (since or {}).get(entity_id)
            self.matched_since.setdefault(entity_id, seed if seed is not None and seed < now else now)
        new_active = tuple(sorted(
            e for e, since in self.matched_since.items()
            if (now - since).total_seconds() >= self.dwell_seconds
        ))
        prev = set(self.active)
        entered = tuple(e for e in new_active if e not in prev)
        left = tuple(sorted(e for e in prev if e not in new_active))
        self.active = new_active
        if new_active and (not prev or self.active_since is None):
            self.active_since = now
        elif not new_active:
            self.active_since = None
        # A restored rule (restore() set `baselined`) compares against the set it
        # held before the restart, so an unchanged set enters nothing. A BRAND-NEW
        # rule has no past: everything it finds on its first evaluation has just
        # entered — that is how "the bulb has been on for two hours, add a rule"
        # turns the bulb off.
        self.baselined = True
        return entered, left

    def next_promotion(self, now: datetime) -> float | None:
        """Seconds until the next matched-but-not-yet-active entity would promote, if any."""
        if self.dwell_seconds <= 0:
            return None
        pending = [
            self.dwell_seconds - (now - since).total_seconds()
            for e, since in self.matched_since.items() if e not in self.active
        ]
        if not pending:
            return None
        return max(0.0, min(pending))


@dataclass
class RuleEngine:
    """The rule's ACTIVE set: the union of what its clauses hold, each with its own dwell."""

    clauses: list[Clause] = field(default_factory=list)
    states: dict[str, RuleState] = field(default_factory=dict)
    active: tuple[str, ...] = ()
    active_since: datetime | None = None
    baselined: bool = False

    def __post_init__(self) -> None:
        for c in self.clauses:
            self.states.setdefault(c.key, RuleState(dwell_seconds=c.dwell_seconds))

    def apply(self, matched_by: dict[str, Iterable[str]], now: datetime,
              since: dict[str, datetime] | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Feed each clause's current match set; returns (entered, left) of the RULE's active set."""
        union: set[str] = set()
        for c in self.clauses:
            st = self.states[c.key]
            st.apply(tuple(matched_by.get(c.key) or ()), now, since)
            union.update(st.active)
        new_active = tuple(sorted(union))
        prev = set(self.active)
        entered = tuple(e for e in new_active if e not in prev)
        left = tuple(sorted(e for e in prev if e not in union))
        self.active = new_active
        if new_active and (not prev or self.active_since is None):
            self.active_since = now
        elif not new_active:
            self.active_since = None
        self.baselined = True     # see RuleState.apply: a restored rule compares with its past, a new one has none
        return entered, left

    def next_promotion(self, now: datetime) -> float | None:
        waits = [w for w in (st.next_promotion(now) for st in self.states.values()) if w is not None]
        return min(waits) if waits else None

    # ---- persistence ----------------------------------------------------------
    def clocks(self) -> dict[str, datetime]:
        """entity → when its earliest still-running clock started (for a countdown in a card)."""
        out: dict[str, datetime] = {}
        for st in self.states.values():
            for e, t in st.matched_since.items():
                if e not in out or t < out[e]:
                    out[e] = t
        return out

    def snapshot(self) -> dict[str, Any]:
        return {
            "clauses": {k: {"matched_since": {e: t.isoformat() for e, t in st.matched_since.items()}} for k, st in self.states.items()},
            "active": list(self.active),
            "active_since": self.active_since.isoformat() if self.active_since else None,
        }

    def restore(self, data: dict[str, Any], parse: Any) -> None:
        """Pre-seed dwell clocks and the active set from before a restart. `parse` turns an ISO
        string into a datetime (HA's parser at runtime). A v1 snapshot — one clock set for the
        whole rule — seeds every clause, so an upgrade does not restart anyone's clock."""
        def clocks(raw: dict[str, str] | None) -> dict[str, datetime]:
            out = {}
            for e, t in (raw or {}).items():
                try:
                    dt = parse(t)
                except (TypeError, ValueError):
                    continue
                if dt is not None:
                    out[e] = dt
            return out

        per_clause = data.get("clauses")
        legacy = clocks(data.get("matched_since")) if per_clause is None else {}
        for key, st in self.states.items():
            st.matched_since = dict(legacy) if per_clause is None else clocks((per_clause.get(key) or {}).get("matched_since"))
        self.active = tuple(sorted(data.get("active") or ()))
        self.active_since = parse(data["active_since"]) if data.get("active_since") else None
        self.baselined = True
