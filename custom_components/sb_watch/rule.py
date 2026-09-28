"""Rule logic, pure: no Home Assistant imports.

A rule is a filter plus a dwell (`for`). SB Filter says which entities match
right now; the rule turns that into an ACTIVE set — an entity is active once
it has matched continuously for `for` — and reports what entered and left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass
class RuleState:
    """Which matched entities have dwelt long enough to be active."""

    dwell_seconds: float = 0.0
    matched_since: dict[str, datetime] = field(default_factory=dict)
    active: tuple[str, ...] = ()
    active_since: datetime | None = None    # when the active set last became non-empty
    baselined: bool = False                 # the first evaluation never "enters" anything

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
        if not self.baselined:
            self.baselined = True
            return (), ()
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
