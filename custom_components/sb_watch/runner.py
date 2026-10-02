"""One RuleRunner per config entry.

The rule's SOURCE says which entities: a named SB Filter (followed in-process,
pushed whenever its set changes), a fixed list of entities, or — the advanced
YAML path — an inline selection with its own SB Filter subscription. The runner watches those entities' states
itself: every trigger is a clause with a state condition (condition.py) and a
dwell; the union of what the clauses hold is the rule's active set. It updates
the entities and fires the change event; the actions hang off that."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from custom_components.sb_filter.ha import FilterSubscription
from custom_components.sb_filter.named import async_listen as listen_named_filter

from .actions import RuleActions
from .condition import Condition, matching, parse_condition
from .const import CONF_NAME, EVENT_CHANGED
from .live import async_prepare_rates, lookup, rate_tracker, rows
from .rule import Clause, Effect, RuleEngine, build_clauses, clean_triggers, rule_source, selection_filter, upgrade_options

DEBOUNCE_SECONDS = 1.0
STATE_FOR_TICK_SECONDS = 30     # "any state for 6h" is measured from last_changed: time moves it, not events
RATE_TICK_SECONDS = 60          # a rate changes as its window slides, even with no new sample


class RuleRunner:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.name: str = entry.options.get(CONF_NAME) or entry.title
        self.options: dict[str, Any] = upgrade_options(dict(entry.options))   # the migration did this already; belt and braces
        self._yaml_error: list[str] = []
        try:
            self.clauses: list[Clause] = build_clauses(self.options)
            self.source: dict[str, Any] = rule_source(self.options)
        except Exception as err:  # noqa: BLE001 — unreadable advanced YAML: the rule matches nothing and says why
            self.clauses = []
            self.source = {}
            self._yaml_error = [f"filter_yaml: {err}"]
        self.selection_config: dict[str, Any] = self.source.get("selection") or {}
        self.conditions: dict[str, Condition] = {c.key: parse_condition(c.condition) for c in self.clauses}
        self.unreadable: list[str] = self._yaml_error + [u for c in self.conditions.values() for u in c.unreadable]
        self.selection: dict[str, Any] = selection_filter(self.options)
        self.triggers: list[dict[str, str]] = [] if self.advanced else clean_triggers(self.options.get("triggers"))
        # what the Count sensor shows as `filter`: the selection, plus the one clause's condition when there is one
        self.filter_config: dict[str, Any] = {**self.selection_config, **(self.clauses[0].condition if len(self.clauses) == 1 else {})}
        self.dwell_seconds = max((c.dwell_seconds for c in self.clauses), default=0.0)
        self.state = RuleEngine(self.clauses)
        self.effect = Effect.from_options(entry.options)
        self._effect_timer: CALLBACK_TYPE | None = None
        self.configured: bool = True
        self.grammar: int | None = None
        self.selected: tuple[str, ...] | None = None          # None until the source has answered once
        self.filter_name: str | None = None
        self.matched_by: dict[str, tuple[str, ...]] = {}
        self.actions = RuleActions(hass, entry.entry_id, self.name, entry.options)
        self.actions.set_active_getter(lambda: self.state.active)
        self._sub: FilterSubscription | None = None
        self._unsub_source: CALLBACK_TYPE | None = None
        self._track: CALLBACK_TYPE | None = None
        self._ticks: list[CALLBACK_TYPE] = []
        self._pending: CALLBACK_TYPE | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []
        self._uses_rates = any(c.rates for c in self.conditions.values())

    @property
    def advanced(self) -> bool:
        """True when the advanced YAML override defines the filter (the triggers are ignored)."""
        return bool(str(self.options.get("filter_yaml") or "").strip())

    @property
    def matched(self) -> tuple[str, ...]:
        """Everything any clause matches right now — before the dwell and the in-effect gate."""
        return tuple(sorted({e for ids in self.matched_by.values() for e in ids}))

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
        if self._sub is not None or self._unsub_source is not None:
            return
        if any(c.state_for for c in self.conditions.values()):
            self._ticks.append(async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=STATE_FOR_TICK_SECONDS)))
        if self._uses_rates:
            self._ticks.append(async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=RATE_TICK_SECONDS)))
        if "filter" in self.source:
            self._unsub_source = listen_named_filter(self.hass, self.source["filter"], self._on_selection)
        elif "entities" in self.source:
            self._unsub_source = lambda: None
            self._on_selection({"ids": sorted(self.source["entities"]), "configured": True})
        else:                                   # the YAML path (or an unmigrated v2 rule): an inline selection
            self._sub = FilterSubscription(self.hass, self.selection_config, self._on_selection, origin=f"rule: {self.name}")
            self._sub.start()

    @callback
    def stop(self) -> None:
        if self._sub is not None:
            self._sub.stop()
            self._sub = None
        if self._unsub_source is not None:
            self._unsub_source()
            self._unsub_source = None
        if self._track:
            self._track()
            self._track = None
        for u in self._ticks:
            u()
        self._ticks = []
        if self._pending:
            self._pending()
            self._pending = None
        if self._uses_rates:
            rate_tracker(self.hass).release(self.entry.entry_id)
        self.selected = None
        self.matched_by = {}
        self._cancel_timer()
        if self._effect_timer:
            self._effect_timer()
            self._effect_timer = None
        self.actions.stop()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # ---- inputs: the selection from SB Filter, states from the bus -----------
    @callback
    def _on_selection(self, payload: dict[str, Any]) -> None:
        ids = tuple(payload.get("ids") or ())
        self.configured = bool(payload.get("configured", True))
        self.unreadable = self._yaml_error + list(payload.get("unreadable") or []) + [u for c in self.conditions.values() for u in c.unreadable]
        self.grammar = payload.get("grammar")
        if payload.get("name"):
            self.filter_name = payload["name"]
        if ids != self.selected:
            if self._track:
                self._track()
                self._track = None
            if ids:
                self._track = async_track_state_change_event(self.hass, list(ids), self._on_state)
        self.selected = ids
        if self._uses_rates:
            self.hass.async_create_task(self._seed_then_evaluate(ids))
        else:
            self._evaluate()

    async def _seed_then_evaluate(self, ids: tuple[str, ...]) -> None:
        await async_prepare_rates(self.hass, self.entry.entry_id, list(self.conditions.values()), ids)
        if self._sub is not None and self.selected == ids:
            self._evaluate()

    @callback
    def _on_state(self, _event: Event) -> None:
        if self._pending is None:
            self._pending = async_call_later(self.hass, DEBOUNCE_SECONDS, HassJob(self._fire, cancel_on_shutdown=True))

    @callback
    def _fire(self, _now=None) -> None:
        self._pending = None
        self._evaluate()

    @callback
    def _on_tick(self, _now=None) -> None:
        self._on_state(None)

    @property
    def in_effect(self) -> bool:
        return self.effect.in_effect(dt_util.now())

    # ---- evaluation ---------------------------------------------------------
    @callback
    def _evaluate(self, _now=None) -> None:
        self._cancel_timer()
        if self.selected is None:           # SB Filter has not answered yet
            return
        now = dt_util.utcnow()
        rs, look = rows(self.hass, self.selected), lookup(self.hass)
        self.matched_by = {c.key: tuple(matching(self.conditions[c.key], rs, self.selected, look, now)) for c in self.clauses}
        # Outside the window / on another day the rule sees nothing: Active off,
        # Count 0, no actions. When the window opens, whatever matches then ENTERS.
        gated = self.matched_by if self.in_effect else {}
        # each matched entity's last_changed seeds its dwell clock the first time it matches
        since = {e: rs[e].last_changed for ids in gated.values() for e in ids if e in rs}
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
            self._timer = async_call_later(self.hass, wait + 0.5, HassJob(self._evaluate, cancel_on_shutdown=True))

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
