"""Staged actions for a rule: notify → notify-then-act → act, with a warn-ahead
delay and the rule's Paused switch as the override.

Notifications REPLACE in place (one per rule, same tag / notification_id) and
are cleared when nothing is active any more. Acting means one
`homeassistant.<turn_off|turn_on|toggle>` call on the entities that just
ENTERED — never on the whole active set again, so a rule can't re-fire on
something the user has since handled.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from functools import partial

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from custom_components.sb_filter.grammar import parse_duration

from .const import CONF_ACT, CONF_ACTION, CONF_NOTIFY_SERVICE, CONF_WARN_AHEAD

from .actions_pure import format_message  # noqa: E402  (HA-free, shared with the tests)


class RuleActions:
    def __init__(self, hass: HomeAssistant, entry_id: str, name: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.name = name
        self.mode = options.get(CONF_ACTION) or "none"
        self.notify_service = (options.get(CONF_NOTIFY_SERVICE) or "").strip()
        self.act = options.get(CONF_ACT) or None
        warn = parse_duration(options.get(CONF_WARN_AHEAD))
        self.warn_seconds = warn.seconds if warn else 0.0
        self.paused = False
        self._pending: dict[str, CALLBACK_TYPE] = {}   # entity_id -> cancel of its scheduled act
        self.last_action: dict[str, Any] | None = None

    @property
    def tag(self) -> str:
        return f"sb_watch_{self.entry_id}"

    # ---- driven by the runner -------------------------------------------------
    @callback
    def on_change(self, entered: tuple[str, ...], left: tuple[str, ...], active: tuple[str, ...], names: list[str]) -> None:
        for entity_id in left:
            self._cancel(entity_id)
        if self.mode == "none" or self.paused:
            return
        if self.mode == "act":
            if entered:
                self._do_act(list(entered))
            return
        # notify / notify_then_act: the notification mirrors the active set
        if active:
            pending_act = self.act if self.mode == "notify_then_act" else None
            self._notify(format_message(names, len(active), pending_act, self.warn_seconds if pending_act else None))
        else:
            self._clear()
        if self.mode == "notify_then_act" and self.act:
            for entity_id in entered:
                if entity_id in self._pending:
                    continue
                if self.warn_seconds <= 0:
                    self._do_act([entity_id])
                else:
                    # A plain lambda is not a @callback: HA would run it in the executor
                    # thread, where async_create_task is illegal. HassJob + a partial of a
                    # @callback method keeps it on the event loop.
                    self._pending[entity_id] = async_call_later(
                        self.hass, self.warn_seconds,
                        HassJob(partial(self._act_if_still, entity_id), cancel_on_shutdown=True),
                    )

    def set_active_getter(self, getter) -> None:
        self._active_getter = getter

    @callback
    def _act_if_still(self, entity_id: str, _now=None) -> None:
        self._pending.pop(entity_id, None)
        if self.paused:
            return
        getter = getattr(self, "_active_getter", None)
        if getter is not None and entity_id not in getter():
            return
        self._do_act([entity_id])

    @callback
    def stop(self) -> None:
        for entity_id in list(self._pending):
            self._cancel(entity_id)

    @callback
    def _cancel(self, entity_id: str) -> None:
        cancel = self._pending.pop(entity_id, None)
        if cancel:
            cancel()

    # ---- the two effects ------------------------------------------------------
    @callback
    def _do_act(self, entity_ids: list[str]) -> None:
        if not self.act or not entity_ids:
            return
        self.last_action = {"act": self.act, "entity_ids": entity_ids}
        self.hass.async_create_task(
            self.hass.services.async_call("homeassistant", self.act, {"entity_id": entity_ids}, blocking=False)
        )

    @callback
    def _notify(self, message: str) -> None:
        if self.notify_service.startswith("notify."):
            domain, service = self.notify_service.split(".", 1)
            data = {"title": self.name, "message": message, "data": {"tag": self.tag, "notification_id": self.tag}}
        else:
            domain, service = "persistent_notification", "create"
            data = {"title": self.name, "message": message, "notification_id": self.tag}
        self.hass.async_create_task(self.hass.services.async_call(domain, service, data, blocking=False))

    @callback
    def clear_notification(self) -> None:
        """Best effort: drop this rule's notification (used when the rule is deleted)."""
        if self.mode in ("notify", "notify_then_act"):
            self._clear()

    @callback
    def _clear(self) -> None:
        if self.notify_service.startswith("notify."):
            domain, service = self.notify_service.split(".", 1)
            data = {"message": "clear_notification", "data": {"tag": self.tag}}
        else:
            domain, service = "persistent_notification", "dismiss"
            data = {"notification_id": self.tag}
        self.hass.async_create_task(self.hass.services.async_call(domain, service, data, blocking=False))
