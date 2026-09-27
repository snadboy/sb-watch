"""One RuleRunner per config entry: subscribes to SB Filter, applies the dwell,
updates entities, fires the change event."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from custom_components.sb_filter.grammar import parse_duration
from custom_components.sb_filter.ha import FilterSubscription

from .actions import RuleActions
from .const import CONF_FOR, CONF_NAME, EVENT_CHANGED
from .rule import RuleState, build_filter


class RuleRunner:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.name: str = entry.options.get(CONF_NAME) or entry.title
        self.filter_config: dict[str, Any] = build_filter(entry.options)
        dwell = parse_duration(entry.options.get(CONF_FOR))
        self.dwell_seconds = dwell.seconds if dwell else 0.0
        self.state = RuleState(dwell_seconds=self.dwell_seconds)
        self.matched: tuple[str, ...] = ()
        self.configured: bool = True
        self.unreadable: list[str] = []
        self.grammar: int | None = None
        self.last_payload: dict[str, Any] | None = None
        self.actions = RuleActions(hass, entry.entry_id, self.name, entry.options)
        self.actions.set_active_getter(lambda: self.state.active)
        self._sub: FilterSubscription | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

    # ---- persistence (the Count sensor stores this as extra restore data) -------
    def snapshot(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry.entry_id,
            "matched_since": {e: t.isoformat() for e, t in self.state.matched_since.items()},
            "active": list(self.state.active),
            "active_since": self.state.active_since.isoformat() if self.state.active_since else None,
        }

    @callback
    def restore(self, data: dict[str, Any] | None) -> None:
        """Pre-seed the dwell clocks from before the restart, so a 2 h dwell does
        not start over at every HA restart. Only data written by THIS entry counts."""
        if not data or data.get("entry_id") != self.entry.entry_id or self.state.baselined:
            return
        since = {}
        for e, t in (data.get("matched_since") or {}).items():
            try:
                since[e] = dt_util.parse_datetime(t)
            except (TypeError, ValueError):
                continue
        self.state.matched_since = {e: t for e, t in since.items() if t is not None}
        self.state.active = tuple(sorted(data.get("active") or ()))
        self.state.active_since = dt_util.parse_datetime(data["active_since"]) if data.get("active_since") else None
        self.state.baselined = True    # what changed during the downtime is a real change

    # ---- lifecycle ----------------------------------------------------------
    @callback
    def start(self) -> None:
        if self._sub is not None:
            return
        self._sub = FilterSubscription(self.hass, self.filter_config, self._on_filter)
        self._sub.start()

    @callback
    def stop(self) -> None:
        if self._sub:
            self._sub.stop()
            self._sub = None
        self._cancel_timer()
        self.actions.stop()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    # ---- evaluation ---------------------------------------------------------
    @callback
    def _on_filter(self, payload: dict[str, Any]) -> None:
        self.last_payload = payload
        self.matched = tuple(payload.get("ids") or ())
        self.configured = bool(payload.get("configured", True))
        self.unreadable = list(payload.get("unreadable") or [])
        self.grammar = payload.get("grammar")
        self._evaluate()

    @callback
    def _evaluate(self, _now=None) -> None:
        self._cancel_timer()
        now = dt_util.utcnow()
        entered, left = self.state.apply(self.matched, now)
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
