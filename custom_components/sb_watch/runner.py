"""One RuleRunner per config entry: one SB Filter subscription per clause (a
trigger), a dwell per clause, the union as the rule's active set; updates
entities, fires the change event."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from custom_components.sb_filter.ha import FilterSubscription

from .actions import RuleActions
from .const import CONF_NAME, EVENT_CHANGED
from .rule import Clause, Effect, RuleEngine, build_clauses, clean_triggers, selection_filter, upgrade_options


class RuleRunner:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.name: str = entry.options.get(CONF_NAME) or entry.title
        self.options: dict[str, Any] = upgrade_options(dict(entry.options))   # the migration did this already; belt and braces
        self.unreadable: list[str] = []
        try:
            self.clauses: list[Clause] = build_clauses(self.options)
        except Exception as err:  # noqa: BLE001 — unreadable advanced YAML: the rule matches nothing and says why
            self.clauses = []
            self.unreadable = [f"filter_yaml: {err}"]
        self.selection: dict[str, Any] = selection_filter(self.options)
        self.triggers: list[dict[str, str]] = [] if self.advanced else clean_triggers(self.options.get("triggers"))
        # what the Count sensor shows as `filter`: the one clause's filter, or — with several — the selection they share
        self.filter_config: dict[str, Any] = self.clauses[0].filter if len(self.clauses) == 1 else self.selection
        self.dwell_seconds = max((c.dwell_seconds for c in self.clauses), default=0.0)
        self.state = RuleEngine(self.clauses)
        self.effect = Effect.from_options(entry.options)
        self._effect_timer: CALLBACK_TYPE | None = None
        self.configured: bool = True
        self.grammar: int | None = None
        self.last_payload: dict[str, dict[str, Any]] = {}
        self.actions = RuleActions(hass, entry.entry_id, self.name, entry.options)
        self.actions.set_active_getter(lambda: self.state.active)
        self._subs: dict[str, FilterSubscription] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def advanced(self) -> bool:
        """True when the advanced YAML override defines the filter (the triggers are ignored)."""
        return bool(str(self.options.get("filter_yaml") or "").strip())

    @property
    def matched(self) -> tuple[str, ...]:
        """Everything any clause matches right now — before the dwell and the in-effect gate."""
        ids: set[str] = set()
        for payload in self.last_payload.values():
            ids.update(payload.get("ids") or ())
        return tuple(sorted(ids))

    # ---- persistence (the Count sensor stores this as extra restore data) -------
    def snapshot(self) -> dict[str, Any]:
        return {"entry_id": self.entry.entry_id, **self.state.snapshot()}

    @callback
    def restore(self, data: dict[str, Any] | None) -> None:
        """Pre-seed the dwell clocks from before the restart, so a 2 h dwell does
        not start over at every HA restart. Only data written by THIS entry counts."""
        if not data or data.get("entry_id") != self.entry.entry_id or self.state.baselined:
            return
        self.state.restore(data, dt_util.parse_datetime)

    # ---- lifecycle ----------------------------------------------------------
    @callback
    def start(self) -> None:
        if self._subs:
            return
        for c in self.clauses:
            sub = FilterSubscription(self.hass, c.filter, partial(self._on_filter, c.key), origin=f"rule: {self.name}")
            self._subs[c.key] = sub
        for sub in list(self._subs.values()):
            sub.start()

    @callback
    def stop(self) -> None:
        for sub in self._subs.values():
            sub.stop()
        self._subs = {}
        self.last_payload = {}
        self._cancel_timer()
        if self._effect_timer:
            self._effect_timer()
            self._effect_timer = None
        self.actions.stop()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # ---- evaluation ---------------------------------------------------------
    @callback
    def _on_filter(self, key: str, payload: dict[str, Any]) -> None:
        self.last_payload[key] = payload
        # Wait until EVERY clause has reported once: evaluating on a partial
        # picture would drop the entities held by a clause that is yet to
        # answer, then re-enter (and re-notify) them a moment later.
        if len(self.last_payload) < len(self.clauses):
            return
        payloads = list(self.last_payload.values())
        self.configured = any(p.get("configured", True) for p in payloads)
        seen: list[str] = []
        for p in payloads:
            for item in p.get("unreadable") or []:
                if item not in seen:
                    seen.append(item)
        self.unreadable = seen
        self.grammar = payloads[0].get("grammar") if payloads else None
        self._evaluate()

    @property
    def in_effect(self) -> bool:
        return self.effect.in_effect(dt_util.now())

    @callback
    def _evaluate(self, _now=None) -> None:
        self._cancel_timer()
        if len(self.last_payload) < len(self.clauses):
            return
        now = dt_util.utcnow()
        # Outside the window / on another day the rule sees nothing: Active off,
        # Count 0, no actions. When the window opens, whatever matches then ENTERS.
        gated = {k: (p.get("ids") or ()) for k, p in self.last_payload.items()} if self.in_effect else {}
        # each matched entity's last_changed seeds its dwell clock the first time it matches
        since = {}
        for ids in gated.values():
            for entity_id in ids:
                if entity_id not in since:
                    st = self.hass.states.get(entity_id)
                    if st is not None:
                        since[entity_id] = st.last_changed
        entered, left = self.state.apply(gated, now, since)
        self._arm_effect_timer()
        if entered or left:
            self.actions.on_change(entered, left, self.state.active, self.names())
            self.hass.bus.async_fire(EVENT_CHANGED, {
                "rule": self.name,
                "entry_id": self.entry.entry_id,
                "entered": list(entered),
                "left": list(left),
                "active": list(self.state.active),
                "count": len(self.state.active),
            })
        for cb in list(self._listeners):
            cb()
        wait = self.state.next_promotion(now)
        if wait is not None:
            self._timer = async_call_later(self.hass, wait + 0.5, self._evaluate)

    @callback
    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer()
            self._timer = None

    @callback
    def _arm_effect_timer(self) -> None:
        """Re-evaluate at the next window edge or midnight, so the gate flips on time."""
        if self._effect_timer:
            self._effect_timer()
            self._effect_timer = None
        nxt = self.effect.next_change(dt_util.now())
        if nxt is None:
            return
        delay = max(1.0, (nxt - dt_util.now()).total_seconds())
        self._effect_timer = async_call_later(self.hass, delay, HassJob(self._on_effect_edge, cancel_on_shutdown=True))

    @callback
    def _on_effect_edge(self, _now=None) -> None:
        self._effect_timer = None
        self._evaluate()

    # ---- for entities -------------------------------------------------------
    @property
    def active(self) -> tuple[str, ...]:
        return self.state.active

    def names(self) -> list[str]:
        out = []
        for entity_id in self.state.active:
            st = self.hass.states.get(entity_id)
            out.append((st.attributes.get("friendly_name") if st else None) or entity_id)
        return out
