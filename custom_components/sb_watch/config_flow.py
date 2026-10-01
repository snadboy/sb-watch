"""Config + options flow in two steps, mirroring the rule's two halves.

Step 1 — WHICH ENTITIES: name, patterns, labels, areas, class:unit pairs (all
as chip lists), plus a collapsed Advanced section (problem flag, the YAML
override and its dwell).
Step 2 — WHEN DO THEY TRIGGER: a list of trigger rows (state / range / rate,
each with its own duration), then the collapsed "in effect" and Actions
sections. The step description lists the states the selection is in right now.

A pasted YAML filter that the model can express is ABSORBED: its selection
fills step 1's fields, its conditions become step 2's trigger rows, and the
YAML box is emptied. Only what the form cannot say stays as YAML.

The SB Watch Card's rule editor and the SB Entity Browser's "save as rule"
drive these same two steps over the REST API; a step-2 post without
`triggers` keeps the rows this flow already holds (the absorbed ones).
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
    ACTIONS, ACTS, CONF_ACT, CONF_ACT_ACTIONS, CONF_ACT_SCRIPT, CONF_ACTION, CONF_AREAS, CONF_CLASSES, CONF_FILTER_YAML, CONF_FOR,
    CONF_LABELS, CONF_NAME, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_PATTERNS, CONF_PROBLEM, CONF_TRIGGERS, CONF_WARN_AHEAD, DOMAIN,
    CONF_DAYS, CONF_DAYS_ENABLED, CONF_WINDOW_ENABLED, CONF_WINDOW_END, CONF_WINDOW_START, OPTIONS_VERSION, WEEKDAYS,
)
from .rule import (
    _yaml_filter, build_clauses, clean_triggers, duration_seconds, model_from_filter, selection_filter, split_list,
    trigger_errors, upgrade_options,
)

SELECTION_KEYS = (CONF_PATTERNS, CONF_LABELS, CONF_AREAS, CONF_CLASSES)
ADVANCED_KEYS = (CONF_PROBLEM, CONF_FILTER_YAML, CONF_FOR)
ACTION_KEYS = (CONF_ACTION, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_ACT, CONF_ACT_SCRIPT, CONF_ACT_ACTIONS, CONF_WARN_AHEAD)
EFFECT_KEYS = (CONF_WINDOW_ENABLED, CONF_WINDOW_START, CONF_WINDOW_END, CONF_DAYS_ENABLED, CONF_DAYS)
COMMON_CLASSES = ("battery:%", "temperature", "humidity:%", "illuminance:lx", "power:W", "energy:kWh", "occupancy", "motion",
                  "door", "window", "moisture", "problem", "connectivity")


def _chips(values: list[str], extra: tuple[str, ...] = ()) -> selector.SelectSelector:
    """A chip list: pick from what is there or type a new entry."""
    opts = list(dict.fromkeys([*values, *extra]))
    return selector.SelectSelector(selector.SelectSelectorConfig(options=opts, multiple=True, custom_value=True,
                                                               mode=selector.SelectSelectorMode.DROPDOWN))


def _step1_schema(d: dict[str, Any]) -> vol.Schema:
    pats, classes = split_list(d.get(CONF_PATTERNS)), split_list(d.get(CONF_CLASSES))
    adv = d.get("advanced") or {k: d.get(k) for k in ADVANCED_KEYS}
    problem = adv.get(CONF_PROBLEM)
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Optional(CONF_PATTERNS, default=pats): _chips(pats),
        vol.Optional(CONF_AREAS, default=list(d.get(CONF_AREAS) or [])): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
        vol.Optional(CONF_LABELS, default=list(d.get(CONF_LABELS) or [])): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
        vol.Optional(CONF_CLASSES, default=classes): _chips(classes, COMMON_CLASSES),
        vol.Optional("advanced"): section(vol.Schema({
            vol.Optional(CONF_PROBLEM, default=True if problem is None else bool(problem)): selector.BooleanSelector(),
            vol.Optional(CONF_FILTER_YAML, default=adv.get(CONF_FILTER_YAML) or ""): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_FOR, default=adv.get(CONF_FOR) or ""): selector.TextSelector(),
        }), {"collapsed": not str(adv.get(CONF_FILTER_YAML) or "").strip()}),
    })


def _clean_step1(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten the Advanced section; chip entries may still carry commas (an old client's one string)."""
    adv = user_input.get("advanced") or {}
    out: dict[str, Any] = {
        CONF_NAME: str(user_input.get(CONF_NAME) or "").strip(),
        CONF_PATTERNS: [p for item in split_list(user_input.get(CONF_PATTERNS)) for p in split_list(item)],
        CONF_LABELS: [s for s in (user_input.get(CONF_LABELS) or []) if s and not str(s).startswith("___")],
        CONF_AREAS: [s for s in (user_input.get(CONF_AREAS) or []) if s and not str(s).startswith("___")],
        CONF_CLASSES: [c for c in split_list(user_input.get(CONF_CLASSES)) if c.strip(": ")],
    }
    for k in ADVANCED_KEYS:      # an old client posts these at the top level
        v = adv.get(k, user_input.get(k))
        out[k] = (True if v is None else bool(v)) if k == CONF_PROBLEM else str(v or "").strip()
    return out


TRIGGER_FIELDS = {
    "kind": {"required": True, "label": "Type", "selector": {"select": {"mode": "dropdown", "options": [
        {"value": "state", "label": "State — a word such as off, open, unavailable (empty = any state)"},
        {"value": "range", "label": "Range — a number: <20, >=80, 15-50, =3"},
        {"value": "rate", "label": "Rate — change per time: >0.5, <-2"}]}}},
    "value": {"label": "State, range or rate value", "selector": {"text": {}}},
    "for": {"label": "For how long (2h, 10m; empty = at once) — for a rate: the window it is measured over", "selector": {"text": {}}},
    "per": {"label": "Rate only: per", "selector": {"select": {"mode": "dropdown", "options": [
        {"value": "m", "label": "minute"}, {"value": "h", "label": "hour"}, {"value": "d", "label": "day"}]}}},
}


def _step2_schema(d: dict[str, Any]) -> vol.Schema:
    actions = d.get("actions") or {k: d.get(k) for k in ACTION_KEYS}
    eff = d.get("effect") or {k: d.get(k) for k in EFFECT_KEYS}
    return vol.Schema({
        vol.Optional(CONF_TRIGGERS, default=clean_triggers(d.get(CONF_TRIGGERS))): selector.ObjectSelector(selector.ObjectSelectorConfig(
            multiple=True, label_field="value", description_field="for", fields=TRIGGER_FIELDS)),
        vol.Optional("effect"): section(vol.Schema({
            vol.Optional(CONF_WINDOW_ENABLED, default=bool(eff.get(CONF_WINDOW_ENABLED))): selector.BooleanSelector(),
            vol.Optional(CONF_WINDOW_START, default=eff.get(CONF_WINDOW_START) or "18:00:00"): selector.TimeSelector(),
            vol.Optional(CONF_WINDOW_END, default=eff.get(CONF_WINDOW_END) or "06:00:00"): selector.TimeSelector(),
            vol.Optional(CONF_DAYS_ENABLED, default=bool(eff.get(CONF_DAYS_ENABLED))): selector.BooleanSelector(),
            vol.Optional(CONF_DAYS, default=list(eff.get(CONF_DAYS) or [])): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": d, "label": l} for d, l in zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))], multiple=True, mode=selector.SelectSelectorMode.LIST)),
        }), {"collapsed": not (eff.get(CONF_WINDOW_ENABLED) or eff.get(CONF_DAYS_ENABLED))}),
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


def _absorb_yaml(step1: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]] | None, dict[str, str]]:
    """(step1, triggers the YAML turned into or None, errors). See the module docstring."""
    if not step1[CONF_FILTER_YAML]:
        if step1[CONF_FOR]:
            step1 = {**step1, CONF_FOR: ""}        # the dwell belongs to the YAML path only
        return step1, None, {}
    try:
        cfg = _yaml_filter(step1)
    except Exception:  # noqa: BLE001
        return step1, None, {"advanced": "bad_yaml"}
    if step1[CONF_FOR] and duration_seconds(step1[CONF_FOR]) is None:
        return step1, None, {"advanced": "bad_duration"}
    model = model_from_filter(cfg, step1[CONF_FOR])
    if model is None:
        return step1, None, {}
    triggers = model.pop(CONF_TRIGGERS)
    return {**step1, **model, CONF_FILTER_YAML: "", CONF_FOR: ""}, triggers, {}


def _merge(step1: dict[str, Any], step2: dict[str, Any]) -> dict[str, Any]:
    actions = step2.get("actions") or {}
    eff = step2.get("effect") or {}
    return {**step1,
            CONF_TRIGGERS: [] if step1.get(CONF_FILTER_YAML) else clean_triggers(step2.get(CONF_TRIGGERS)),
            **{k: actions.get(k) for k in ACTION_KEYS}, **{k: eff.get(k) for k in EFFECT_KEYS}}


def _live(hass, step1: dict[str, Any]) -> dict[str, str]:
    """What the selection holds right now, for the step-2 description."""
    sel = selection_filter(step1) if not step1.get(CONF_FILTER_YAML) else {}
    if not sel:
        return {"selected": "", "vocab": ""}
    _, res = match_now(hass, sel)
    vocab = [v for v in values_now(hass, sel) if v.get("current")]
    vocab.sort(key=lambda v: -v["current"])
    words = " · ".join(f"{v['label']} {v['current']}" for v in vocab[:12])
    n = len(res.ids)
    return {"selected": f"{n} {'entity' if n == 1 else 'entities'} selected.", "vocab": f" In these states now: {words}." if words else ""}


def _validate_all(hass, options: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Errors and description placeholders for the merged rule."""
    errors: dict[str, str] = {}
    ph: dict[str, str] = {"unmatched": ""}
    problems = trigger_errors(options.get(CONF_TRIGGERS) or [])
    if problems:
        ph["unmatched"] = "; ".join(problems)
        return {CONF_TRIGGERS: "bad_trigger"}, ph
    try:
        clauses = build_clauses(options)
    except Exception:  # noqa: BLE001
        return {"base": "bad_yaml"}, ph
    parsed = [parse_filter(c.filter) for c in clauses]
    if not any(f.configured for f in parsed):
        return {"base": "empty_filter"}, ph
    unreadable = [u for f in parsed for u in f.unreadable]
    if unreadable:
        ph["unmatched"] = "; ".join(unreadable)
        return {CONF_TRIGGERS: "bad_trigger"}, ph
    unmatched = []
    for c in clauses:
        _, res = match_now(hass, c.filter)
        unmatched += [f"{u.value}" + (f" (did you mean {', '.join(u.suggestions)}?)" if u.suggestions else "") for u in res.unmatched_values]
    if unmatched:
        errors[CONF_TRIGGERS] = "unknown_value"
        ph["unmatched"] = "; ".join(dict.fromkeys(unmatched))
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
            step1, absorbed, errors = _absorb_yaml(_clean_step1(user_input))
            if not step1[CONF_NAME]:
                errors[CONF_NAME] = "no_name"
            if not errors:
                self._step1 = step1
                if absorbed is not None:
                    self._defaults = {**self._defaults, CONF_TRIGGERS: absorbed}
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
        return self.async_show_form(step_id="values", data_schema=_step2_schema(d), errors=errors,
                                    description_placeholders={**_live(self.hass, self._step1), **ph})


class SbWatchConfigFlow(_TwoStep, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = OPTIONS_VERSION

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
        if not self._defaults:
            self._defaults = upgrade_options(dict(self.config_entry.options))
        return await self._do_step1("init", user_input)

    async def async_step_values(self, user_input: dict[str, Any] | None = None):
        return await self._do_step2(user_input)

    def _finish(self, merged: dict[str, Any]):
        self.hass.config_entries.async_update_entry(self.config_entry, title=merged[CONF_NAME])
        return self.async_create_entry(title="", data=merged)
