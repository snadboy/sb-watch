"""Config + options flow: one form, pickers for labels/areas, text for the rest,
and an advanced YAML mapping that overrides the fields."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from custom_components.sb_filter.grammar import parse_duration, parse_filter

from .const import (
    CONF_AREAS, CONF_DEVICE_CLASSES, CONF_FILTER_YAML, CONF_FOR, CONF_LABELS, CONF_NAME,
    CONF_PATTERNS, CONF_PROBLEM, CONF_STATE_FOR, CONF_STATES, CONF_UNITS, DOMAIN,
)
from .rule import build_filter


def _schema(defaults: dict[str, Any], with_name: bool) -> vol.Schema:
    d = defaults
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=d.get(CONF_NAME, ""))] = selector.TextSelector()
    fields.update({
        vol.Optional(CONF_PATTERNS, default=d.get(CONF_PATTERNS, "")): selector.TextSelector(),
        vol.Optional(CONF_LABELS, default=d.get(CONF_LABELS, [])): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
        vol.Optional(CONF_AREAS, default=d.get(CONF_AREAS, [])): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
        vol.Optional(CONF_DEVICE_CLASSES, default=d.get(CONF_DEVICE_CLASSES, "")): selector.TextSelector(),
        vol.Optional(CONF_UNITS, default=d.get(CONF_UNITS, "")): selector.TextSelector(),
        vol.Optional(CONF_STATES, default=d.get(CONF_STATES, "")): selector.TextSelector(),
        vol.Optional(CONF_STATE_FOR, default=d.get(CONF_STATE_FOR, "")): selector.TextSelector(),
        vol.Optional(CONF_FOR, default=d.get(CONF_FOR, "")): selector.TextSelector(),
        vol.Optional(CONF_PROBLEM, default=d.get(CONF_PROBLEM, True)): selector.BooleanSelector(),
        vol.Optional(CONF_FILTER_YAML, default=d.get(CONF_FILTER_YAML, "")): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
    })
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        cfg = build_filter(user_input)
    except Exception:  # noqa: BLE001 - yaml errors of every shape
        return {CONF_FILTER_YAML: "bad_yaml"}
    flt = parse_filter(cfg)
    if not flt.configured:
        errors["base"] = "empty_filter"
    if flt.unreadable:
        errors[CONF_STATE_FOR] = "bad_duration"
    if user_input.get(CONF_FOR) and parse_duration(user_input[CONF_FOR]) is None:
        errors[CONF_FOR] = "bad_duration"
    return errors


class SbWatchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                name = user_input[CONF_NAME].strip()
                return self.async_create_entry(title=name, data={}, options={**user_input, CONF_NAME: name})
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}, True), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return SbWatchOptionsFlow()


class SbWatchOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                name = user_input[CONF_NAME].strip()
                self.hass.config_entries.async_update_entry(self.config_entry, title=name)
                return self.async_create_entry(title="", data={**user_input, CONF_NAME: name})
        return self.async_show_form(
            step_id="init", data_schema=_schema(user_input or dict(self.config_entry.options), True), errors=errors
        )
