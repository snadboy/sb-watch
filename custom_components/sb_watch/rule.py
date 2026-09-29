"""Rule logic, pure: no Home Assistant imports.

A rule is a filter plus a dwell (`for`). SB Filter says which entities match
right now; the rule turns that into an ACTIVE set — an entity is active once
it has matched continuously for `for` — and reports what entered and left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

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

    def apply(self, matched: list[str] | tuple[str, ...], now: datetime) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Feed the current match set; returns (entered, left) relative to the previous ACTIVE set."""
        matched_set = set(matched)
        for entity_id in list(self.matched_since):
            if entity_id not in matched_set:
                del self.matched_since[entity_id]
        for entity_id in matched_set:
            self.matched_since.setdefault(entity_id, now)
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
