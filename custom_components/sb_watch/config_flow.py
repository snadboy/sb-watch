"""Config + options flow in two steps.

Step 1 (filter): name, patterns, labels, areas, device classes, units, time in
state, the rule's dwell, and the advanced YAML override.
Step 2 (values): the STATES as a multi-select built live from SB Filter's
vocabulary of what step 1 selects (translated label, live count; raw value
stored) — custom entries allowed for ranges and numbers — plus min/max and a
collapsed Actions section.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from custom_components.sb_filter.grammar import parse_duration, parse_filter
from custom_components.sb_filter.ha import match_now, values_now

from .const import (
    ACTIONS, ACTS, CONF_ACT, CONF_ACT_ACTIONS, CONF_ACT_SCRIPT, CONF_ACTION, CONF_AREAS, CONF_DEVICE_CLASSES, CONF_FILTER_YAML, CONF_FOR,
    CONF_LABELS, CONF_NAME, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_PATTERNS, CONF_PROBLEM, CONF_RATE, CONF_RATE_WINDOW,
    CONF_STATE_FOR, CONF_STATES, CONF_UNITS, CONF_WARN_AHEAD, DOMAIN,
)
from .rule import build_filter, split_list

STEP1_KEYS = (CONF_NAME, CONF_PATTERNS, CONF_LABELS, CONF_AREAS, CONF_DEVICE_CLASSES, CONF_UNITS,
              CONF_STATE_FOR, CONF_RATE, CONF_RATE_WINDOW, CONF_FOR, CONF_PROBLEM, CONF_FILTER_YAML)
ACTION_KEYS = (CONF_ACTION, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_ACT, CONF_ACT_SCRIPT, CONF_ACT_ACTIONS, CONF_WARN_AHEAD)


def _step1_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Optional(CONF_PATTERNS, default=d.get(CONF_PATTERNS, "")): selector.TextSelector(),
        vol.Optional(CONF_LABELS, default=d.get(CONF_LABELS, [])): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
        vol.Optional(CONF_AREAS, default=d.get(CONF_AREAS, [])): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
        vol.Optional(CONF_DEVICE_CLASSES, default=d.get(CONF_DEVICE_CLASSES, "")): selector.TextSelector(),
        vol.Optional(CONF_UNITS, default=d.get(CONF_UNITS, "")): selector.TextSelector(),
        vol.Optional(CONF_STATE_FOR, default=d.get(CONF_STATE_FOR, "")): selector.TextSelector(),
        vol.Optional(CONF_RATE, default=d.get(CONF_RATE, "")): selector.TextSelector(),
        vol.Optional(CONF_RATE_WINDOW, default=d.get(CONF_RATE_WINDOW, "")): selector.TextSelector(),
        vol.Optional(CONF_FOR, default=d.get(CONF_FOR, "")): selector.TextSelector(),
        vol.Optional(CONF_PROBLEM, default=d.get(CONF_PROBLEM, True)): selector.BooleanSelector(),
        vol.Optional(CONF_FILTER_YAML, default=d.get(CONF_FILTER_YAML, "")): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
    })


def _scope(step1: dict[str, Any]) -> dict[str, Any]:
    """The filter without its state terms: what the values are drawn from."""
    cfg = build_filter({**step1, CONF_STATES: None})
    for k in ("states", "state_min", "state_max", "state_for", "rate", "rate_window"):
        cfg.pop(k, None)
    return cfg


def _step2_schema(hass, step1: dict[str, Any], d: dict[str, Any]) -> vol.Schema:
    vocab = values_now(hass, _scope(step1)) if _scope(step1) else []
    current = split_list(d.get(CONF_STATES))
    options = [{"value": v["value"], "label": f"{v['label']} ({v['current']} now)" if v["label"].lower() == v["value"].lower()
                else f"{v['label']} — {v['value']} ({v['current']} now)"} for v in vocab[:80]]
    known = {o["value"].lower() for o in options}
    for s in current:                      # ranges / numbers / anything typed earlier stay selectable
        if s.lower() not in known:
            options.append({"value": s, "label": s}); known.add(s.lower())
    actions = d.get("actions") or {k: d.get(k) for k in ACTION_KEYS}
    return vol.Schema({
        vol.Optional(CONF_STATES, default=current): selector.SelectSelector(selector.SelectSelectorConfig(
            options=options, multiple=True, custom_value=True, mode=selector.SelectSelectorMode.LIST if len(options) <= 12 else selector.SelectSelectorMode.DROPDOWN)),
        vol.Optional("state_min", default=d.get("state_min", "")): selector.TextSelector(),
        vol.Optional("state_max", default=d.get("state_max", "")): selector.TextSelector(),
        vol.Optional("actions"): section(vol.Schema({
            vol.Optional(CONF_ACTION, default=actions.get(CONF_ACTION) or "none"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": a, "label": l} for a, l in (("none", "No action (entities and event only)"), ("notify", "Notify"),
                         ("notify_then_act", "Notify, then act after the warn-ahead"), ("act", "Act at once"))],
                mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_NOTIFY_SERVICE, default=actions.get(CONF_NOTIFY_SERVICE) or ""): selector.TextSelector(),
            vol.Optional(CONF_NOTIFY_URL, default=actions.get(CONF_NOTIFY_URL) or ""): selector.TextSelector(),
            vol.Optional(CONF_ACT, default=actions.get(CONF_ACT) or "turn_off"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": a, "label": l} for a, l in (("turn_off", "Turn off"), ("turn_on", "Turn on"), ("toggle", "Toggle"), ("run_script", "Run a script"), ("run_actions", "Run these actions"))], mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_ACT_SCRIPT, default=actions.get(CONF_ACT_SCRIPT) or vol.UNDEFINED): selector.EntitySelector(selector.EntitySelectorConfig(domain="script")),
            vol.Optional(CONF_ACT_ACTIONS, default=actions.get(CONF_ACT_ACTIONS) or []): selector.ActionSelector(),
            vol.Optional(CONF_WARN_AHEAD, default=actions.get(CONF_WARN_AHEAD) or "10m"): selector.TextSelector(),
        }), {"collapsed": (actions.get(CONF_ACTION) or "none") == "none"}),
    })


def _validate_step1(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        cfg = build_filter(user_input)
    except Exception:  # noqa: BLE001
        return {CONF_FILTER_YAML: "bad_yaml"}
    flt = parse_filter(cfg)
    for item in flt.unreadable:
        if item.startswith("rate:"):
            errors[CONF_RATE] = "bad_rate"
        elif item.startswith("rate_window:"):
            errors[CONF_RATE_WINDOW] = "bad_duration"
        else:
            errors[CONF_STATE_FOR] = "bad_duration"
    if user_input.get(CONF_FOR) and parse_duration(user_input[CONF_FOR]) is None:
        errors[CONF_FOR] = "bad_duration"
    return errors


def _merge(step1: dict[str, Any], step2: dict[str, Any]) -> dict[str, Any]:
    actions = step2.get("actions") or {}
    out = {**step1, CONF_STATES: split_list(step2.get(CONF_STATES)),
           "state_min": step2.get("state_min") or "", "state_max": step2.get("state_max") or "", **{k: actions.get(k) for k in ACTION_KEYS}}
    out[CONF_NAME] = str(out.get(CONF_NAME, "")).strip()
    return out


def _validate_all(hass, options: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Errors and description placeholders for the merged rule."""
    errors: dict[str, str] = {}
    ph: dict[str, str] = {"unmatched": ""}
    try:
        cfg = build_filter(options)
    except Exception:  # noqa: BLE001
        return {"base": "bad_yaml"}, ph
    flt = parse_filter(cfg)
    if not flt.configured:
        errors["base"] = "empty_filter"
        return errors, ph
    _, res = match_now(hass, cfg)
    if res.unmatched_values:
        errors[CONF_STATES] = "unknown_value"
        ph["unmatched"] = "; ".join(f"{u.value}" + (f" (did you mean {', '.join(u.suggestions)}?)" if u.suggestions else "") for u in res.unmatched_values)
    mode = options.get(CONF_ACTION) or "none"
    if mode not in ACTIONS:
        errors["base"] = "bad_action"
    ns = (options.get(CONF_NOTIFY_SERVICE) or "").strip()
    if ns and not ns.startswith("notify."):
        errors["base"] = "bad_notify"
    if mode in ("notify_then_act", "act") and (options.get(CONF_ACT) or "") not in ACTS:
        errors["base"] = "bad_act"
    if mode in ("notify_then_act", "act") and options.get(CONF_ACT) == "run_script" and not str(options.get(CONF_ACT_SCRIPT) or "").startswith("script."):
        errors["base"] = "bad_script"
    if mode in ("notify_then_act", "act") and options.get(CONF_ACT) == "run_actions" and not options.get(CONF_ACT_ACTIONS):
        errors["base"] = "bad_actions"
    if mode == "notify_then_act" and options.get(CONF_WARN_AHEAD) and parse_duration(options[CONF_WARN_AHEAD]) is None:
        errors["base"] = "bad_duration"
    return errors, ph


class _TwoStep:
    """Shared by the config and options flows."""

    _step1: dict[str, Any] = {}
    _defaults: dict[str, Any] = {}

    async def _do_step1(self, step_id: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_step1(user_input)
            if not errors:
                self._step1 = {k: user_input.get(k) for k in STEP1_KEYS}
                return await self.async_step_values()
        return self.async_show_form(step_id=step_id, data_schema=_step1_schema(user_input or self._defaults), errors=errors)

    async def _do_step2(self, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        ph = {"unmatched": ""}
        if user_input is not None:
            merged = _merge(self._step1, user_input)
            errors, ph = _validate_all(self.hass, merged)
            if not errors:
                return self._finish(merged)
        d = {**self._defaults, **(user_input or {})}
        return self.async_show_form(step_id="values", data_schema=_step2_schema(self.hass, self._step1, d),
                                    errors=errors, description_placeholders=ph)


class SbWatchConfigFlow(_TwoStep, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._step1 = {}
        self._defaults = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._do_step1("user", user_input)

    async def async_step_values(self, user_input: dict[str, Any] | None = None):
        return await self._do_step2(user_input)

    def _finish(self, merged: dict[str, Any]):
        return self.async_create_entry(title=merged[CONF_NAME], data={}, options=merged)

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return SbWatchOptionsFlow()


class SbWatchOptionsFlow(_TwoStep, config_entries.OptionsFlow):
    def __init__(self) -> None:
        self._step1 = {}
        self._defaults = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._defaults = dict(self.config_entry.options)
        return await self._do_step1("init", user_input)

    async def async_step_values(self, user_input: dict[str, Any] | None = None):
        return await self._do_step2(user_input)

    def _finish(self, merged: dict[str, Any]):
        self.hass.config_entries.async_update_entry(self.config_entry, title=merged[CONF_NAME])
        return self.async_create_entry(title="", data=merged)
