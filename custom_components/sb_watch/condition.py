"""State conditions, pure: no Home Assistant imports.

SB Filter answers WHICH entities (patterns, labels, areas, device class, unit).
Everything about their STATE lives here: word states (raw or HA's translated
alias), numeric ranges and equality, time in the current state, and rate of
change. A rule's trigger row is one condition; the YAML override may combine
several keys, which are ANDed exactly as SB Filter's grammar 4 did:

  states (words OR ranges)  ∧  state_for  ∧  rate (terms ORed)

Three kinds of state, decided by the state itself: a numeric state meets only
ranges / numeric equality, a word state meets only words. A word is checked
against the selected entities' vocabularies (HA's translation tables, enum
options, the current state) so a typo is caught with a did-you-mean.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

PLACEHOLDER_PREFIX = "___"
CONDITION_KEYS = ("states", "state_min", "state_max", "state_for", "rate", "rate_window")
SELECTION_KEYS = ("patterns", "labels", "areas", "device_classes", "units", "classes")

_RANGE_RX = re.compile(r"^(?:(<=|<|>=|>)\s*(-?\d+(?:\.\d+)?)|(-?\d+(?:\.\d+)?)\s*(?:\.\.|-)\s*(-?\d+(?:\.\d+)?))$")
_DUR_RX = re.compile(r"^(<=|<|>=|>)?\s*((?:\d+(?:\.\d+)?\s*[dhms]\s*)+|\d+(?:\.\d+)?)$", re.I)
_DUR_PART = re.compile(r"([\d.]+)([dhms])", re.I)
_RATE_RX = re.compile(r"^(<=|<|>=|>)\s*(-?\d+(?:\.\d+)?)\s*/\s*([mhd])$", re.I)
_UNIT_SECS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def as_list(v: Any) -> list[str]:
    """A list, a comma string, one value, or nothing → list of stripped strings (placeholders dropped)."""
    if v is None or v == "":
        return []
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    out = []
    for s in items:
        s = str(s if s is not None else "").strip()
        if s and not s.startswith(PLACEHOLDER_PREFIX):
            out.append(s)
    return out


def is_number(s: Any) -> float | None:
    """The float value of a numeric state, else None (``inf``/``nan`` are not states we range over)."""
    try:
        n = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


@dataclass(frozen=True)
class Range:
    lo: float | None = None
    hi: float | None = None
    lo_open: bool = False   # True: strictly greater than lo
    hi_open: bool = False   # True: strictly less than hi

    def contains(self, n: float) -> bool:
        if self.lo is not None and (n <= self.lo if self.lo_open else n < self.lo):
            return False
        if self.hi is not None and (n >= self.hi if self.hi_open else n > self.hi):
            return False
        return True


def parse_range(s: Any) -> Range | None:
    """``<20 <=20 >80 >=80 20-50 20..50``; a span is inclusive and may be written either way round."""
    m = _RANGE_RX.match(str(s).strip())
    if not m:
        return None
    if m.group(3) is not None:
        a, b = float(m.group(3)), float(m.group(4))
        return Range(lo=min(a, b), hi=max(a, b))
    n = float(m.group(2))
    return {"<": Range(hi=n, hi_open=True), "<=": Range(hi=n), ">": Range(lo=n, lo_open=True), ">=": Range(lo=n)}[m.group(1)]


@dataclass(frozen=True)
class Duration:
    op: str        # < <= > >=
    seconds: float

    def holds(self, age_seconds: float) -> bool:
        return {"<": age_seconds < self.seconds, "<=": age_seconds <= self.seconds,
                ">": age_seconds > self.seconds, ">=": age_seconds >= self.seconds}[self.op]


def parse_duration(v: Any) -> Duration | None:
    """``2h`` / ``>=2h`` at least; ``<5m`` within; d h m s combine (``1h30m``); a bare number is MINUTES."""
    if v is None or v == "":
        return None
    m = _DUR_RX.match(str(v).strip())
    if not m:
        return None
    body = re.sub(r"\s+", "", m.group(2))
    if re.fullmatch(r"[\d.]+", body):
        secs = float(body) * 60
    else:
        secs = sum(float(n) * _UNIT_SECS[u.lower()] for n, u in _DUR_PART.findall(body))
    return Duration(op=m.group(1) or ">=", seconds=secs)


@dataclass(frozen=True)
class RateTerm:
    op: str            # < <= > >=
    value: float       # in units per `per`
    per: str           # m h d
    per_seconds: float

    def holds(self, per_second: float) -> bool:
        r = per_second * self.per_seconds
        return {"<": r < self.value, "<=": r <= self.value, ">": r > self.value, ">=": r >= self.value}[self.op]


def parse_rate(v: Any) -> RateTerm | None:
    """``>0.5/h`` ``<-2/h`` ``>=1/m`` ``<0.1/d`` — change per minute/hour/day; comparator required."""
    if v is None or v == "":
        return None
    m = _RATE_RX.match(str(v).strip())
    if not m:
        return None
    per = m.group(3).lower()
    return RateTerm(op=m.group(1), value=float(m.group(2)), per=per, per_seconds=_UNIT_SECS[per])


@dataclass(frozen=True)
class Condition:
    values: tuple[str, ...] = ()                # lower-cased WORD values (raw or translated)
    value_text: tuple[str, ...] = ()            # the same words as typed, for the unmatched report
    ranges: tuple[Range, ...] = ()
    state_for: Duration | None = None
    rates: tuple[RateTerm, ...] = ()            # ORed; only numeric states have a rate
    rate_window: float | None = None            # seconds; default = the largest unit among the terms
    unreadable: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """No condition: every selected entity holds."""
        return not (self.values or self.ranges or self.state_for or self.rates)


def parse_condition(c: dict[str, Any] | None) -> Condition:
    """The state keys of a config (states, state_min, state_max, state_for, rate, rate_window)."""
    c = c or {}
    unreadable: list[str] = []
    values: list[str] = []
    ranges: list[Range] = []
    for s in as_list(c.get("states")):
        r = parse_range(s)
        n = is_number(s) if r is None else None
        if r:
            ranges.append(r)
        elif n is not None:
            ranges.append(Range(lo=n, hi=n))          # a plain number is numeric EQUALITY
        else:
            values.append(s.lower())
    lo, hi = is_number(c.get("state_min")), is_number(c.get("state_max"))
    if lo is not None or hi is not None:
        ranges.append(Range(lo=lo, hi=hi))
    dur = parse_duration(c.get("state_for"))
    if c.get("state_for") not in (None, "") and dur is None:
        unreadable.append(f"state_for: {c.get('state_for')}")
    rates: list[RateTerm] = []
    for r in as_list(c.get("rate")):
        term = parse_rate(r)
        if term:
            rates.append(term)
        else:
            unreadable.append(f"rate: {r}")
    window = None
    if rates:
        w = parse_duration(c.get("rate_window"))
        if c.get("rate_window") not in (None, "") and w is None:
            unreadable.append(f"rate_window: {c.get('rate_window')}")
        window = w.seconds if w else max(t.per_seconds for t in rates)
    return Condition(
        values=tuple(values),
        value_text=tuple(w for w in as_list(c.get("states")) if parse_range(w) is None and is_number(w) is None),
        ranges=tuple(ranges), state_for=dur, rates=tuple(rates), rate_window=window, unreadable=tuple(unreadable),
    )


# ---- evaluation -------------------------------------------------------------------
@dataclass(frozen=True)
class StateRow:
    state: str
    attributes: dict
    last_changed: datetime       # aware


@dataclass
class Lookup:
    """What a condition may ask about an entity beyond its state row (HA glue fills these in)."""

    # entity_id -> displayed (translated) state for NON-numeric states
    formatted: Callable[[str], str | None] = lambda entity_id: None
    # entity_id -> the states it can be in, (raw, translated-or-None) pairs
    vocabulary: Callable[[str], list[tuple[str, str | None]]] = lambda entity_id: []
    # entity_id, window_seconds -> numeric samples (time, value) ascending
    history: Callable[[str, float], list[tuple[datetime, float]]] = lambda entity_id, window: []


def rate_per_second(samples: list[tuple[datetime, float]], now: datetime, now_value: float, window: float) -> float | None:
    """(value now − the value HELD at the window's start) / window, or None without a sample that old."""
    if window <= 0:
        return None
    cutoff = now - timedelta(seconds=window)
    ref = None
    for ts, v in samples:
        if ts <= cutoff:
            ref = v
        else:
            break
    if ref is None:
        return None
    return (now_value - ref) / window


def holds(cond: Condition, entity_id: str, row: StateRow, look: Lookup, now: datetime) -> bool:
    """Every configured part must hold; an empty condition always does."""
    if cond.values or cond.ranges:
        n = is_number(row.state)
        if n is not None:
            hit = any(r.contains(n) for r in cond.ranges)
        else:
            fmt = look.formatted(entity_id)
            hit = row.state.lower() in cond.values or (fmt is not None and fmt.lower() in cond.values)
        if not hit:
            return False
    if cond.state_for is not None and not cond.state_for.holds((now - row.last_changed).total_seconds()):
        return False
    if cond.rates:
        n = is_number(row.state)
        if n is None:
            return False
        r = rate_per_second(look.history(entity_id, cond.rate_window or 0), now, n, cond.rate_window or 0)
        if r is None or not any(term.holds(r) for term in cond.rates):
            return False
    return True


def matching(cond: Condition, rows: dict[str, StateRow], ids: list[str] | tuple[str, ...], look: Lookup, now: datetime) -> list[str]:
    """The ids (of those given) whose state meets the condition, in order."""
    return [e for e in ids if e in rows and holds(cond, e, rows[e], look, now)]


# ---- vocabulary: chips, suggestions, typo checks ------------------------------------
@dataclass(frozen=True)
class Unmatched:
    value: str
    suggestions: tuple[str, ...]


def entity_vocabulary(look: Lookup, entity_id: str, row: StateRow) -> list[tuple[str, str | None]]:
    """Every state this entity can be in: HA's table for it, plus its current raw state."""
    if is_number(row.state) is not None:
        return []
    vocab = list(look.vocabulary(entity_id) or [])
    if row.state.lower() not in {r.lower() for r, _ in vocab}:
        vocab.append((row.state, look.formatted(entity_id)))
    return vocab


def unmatched_values(cond: Condition, rows: dict[str, StateRow], selected: list[str] | tuple[str, ...], look: Lookup) -> tuple[Unmatched, ...]:
    """Words that are in NO selected entity's vocabulary, with did-you-mean from that vocabulary."""
    if not cond.value_text:
        return ()
    words: dict[str, tuple[str, str]] = {}   # lower word -> (as spelled, raw state key)
    for entity_id in selected:
        row = rows.get(entity_id)
        if row is None:
            continue
        for raw, translated in entity_vocabulary(look, entity_id, row):
            for w in (translated, raw):        # translated first: it is the form to suggest
                if w:
                    words.setdefault(w.lower(), (w, raw.lower()))
    out = []
    for typed in cond.value_text:
        if typed.lower() in words:
            continue
        seen: set[str] = set()
        picks: list[str] = []
        for c in difflib.get_close_matches(typed.lower(), list(words), n=6, cutoff=0.75):
            spelled, key = words[c]
            if key not in seen:                  # one suggestion per underlying state
                seen.add(key)
                picks.append(spelled)
        out.append(Unmatched(value=typed, suggestions=tuple(picks[:3])))
    return tuple(out)


def values(rows: dict[str, StateRow], selected: list[str] | tuple[str, ...], look: Lookup) -> list[dict]:
    """The vocabulary of the selected entities, with how many are in each state now — for chips."""
    table: dict[str, dict] = {}
    for entity_id in selected:
        row = rows.get(entity_id)
        if row is None:
            continue
        for raw, translated in entity_vocabulary(look, entity_id, row):
            key = raw.lower()
            item = table.setdefault(key, {"value": raw, "label": translated or raw, "possible": 0, "current": 0})
            item["possible"] += 1
            if row.state.lower() == key:
                item["current"] += 1
    return sorted(table.values(), key=lambda i: (-i["current"], -i["possible"], i["label"].lower()))
