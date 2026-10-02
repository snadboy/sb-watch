"""Config + options flow: ONE page, laid out like the SB Watch Card's rule editor.

  Rule name
  ▾ Which entities          patterns, areas, labels, class:unit pairs (chip lists)
  ▾ When do they trigger    a list of rows — state / range / rate, each with its duration
  ▸ When the rule is in effect
  ▸ Actions
  ▸ Advanced                problem flag, the YAML override and its dwell

(0.9.x was two steps — selection, then triggers. Opening Configure showed only
the selection, and the comparison everyone looks for — "<20" — was a page away.)

A pasted YAML filter that the model can express is ABSORBED on submit: its
selection fills the chips, its conditions become the trigger rows, and the
YAML box is emptied. Only what the form cannot say stays as YAML.

The SB Watch Card's rule editor (and the sidebar panel built from it) posts
this same single step over the REST API.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from custom_components.sb_filter.grammar import parse_filter

from .condition import parse_condition, parse_duration
from .live import selected_ids, unmatched_now, values_now

from .const import (
    ACTIONS, ACTS, CONF_ACT, CONF_ACT_ACTIONS, CONF_ACT_SCRIPT, CONF_ACTION, CONF_AREAS, CONF_CLASSES, CONF_FILTER_YAML, CONF_FOR,
    CONF_LABELS, CONF_NAME, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_PATTERNS, CONF_PROBLEM, CONF_TRIGGERS, CONF_WARN_AHEAD, DOMAIN,
    CONF_DAYS, CONF_DAYS_ENABLED, CONF_WINDOW_ENABLED, CONF_WINDOW_END, CONF_WINDOW_START, OPTIONS_VERSION, WEEKDAYS,
)
from .rule import (
    _yaml_filter, build_clauses, clean_triggers, duration_seconds, model_from_filter, rule_selection, selection_filter, split_list,
    trigger_errors, upgrade_options,
)

SELECTION_KEYS = (CONF_PATTERNS, CONF_LABELS, CONF_AREAS, CONF_CLASSES)
ADVANCED_KEYS = (CONF_PROBLEM, CONF_FILTER_YAML, CONF_FOR)
ACTION_KEYS = (CONF_ACTION, CONF_NOTIFY_SERVICE, CONF_NOTIFY_URL, CONF_ACT, CONF_ACT_SCRIPT, CONF_ACT_ACTIONS, CONF_WARN_AHEAD)
EFFECT_KEYS = (CONF_WINDOW_ENABLED, CONF_WINDOW_START, CONF_WINDOW_END, CONF_DAYS_ENABLED, CONF_DAYS)
SECTIONS = {"selection": SELECTION_KEYS, "trigger": (CONF_TRIGGERS,), "effect": EFFECT_KEYS, "actions": ACTION_KEYS, "advanced": ADVANCED_KEYS}
COMMON_CLASSES = ("battery:%", "temperature", "humidity:%", "illuminance:lx", "power:W", "energy:kWh", "occupancy", "motion",
                  "door", "window", "moisture", "problem", "connectivity")

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


def _chips(values: list[str], extra: tuple[str, ...] = ()) -> selector.SelectSelector:
    """A chip list: pick from what is there or type a new entry."""
    opts = list(dict.fromkeys([*values, *extra]))
    return selector.SelectSelector(selector.SelectSelectorConfig(options=opts, multiple=True, custom_value=True,
                                                               mode=selector.SelectSelectorMode.DROPDOWN))


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """The form's sections → one flat dict (a key posted at the top level is taken as it is)."""
    flat = {k: v for k, v in user_input.items() if k not in SECTIONS}
    for sec in SECTIONS:
        flat.update(user_input.get(sec) or {})
    return flat


def _schema(d: dict[str, Any]) -> vol.Schema:
    """`d` is FLAT (stored options, or a flattened submission being shown again)."""
    pats, classes = split_list(d.get(CONF_PATTERNS)), split_list(d.get(CONF_CLASSES))
    problem = d.get(CONF_PROBLEM)
    actions = {k: d.get(k) for k in ACTION_KEYS}
    eff = {k: d.get(k) for k in EFFECT_KEYS}
    has_yaml = bool(str(d.get(CONF_FILTER_YAML) or "").strip())
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Optional("selection"): section(vol.Schema({
            vol.Optional(CONF_PATTERNS, default=pats): _chips(pats),
            vol.Optional(CONF_AREAS, default=list(d.get(CONF_AREAS) or [])): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
            vol.Optional(CONF_LABELS, default=list(d.get(CONF_LABELS) or [])): selector.LabelSelector(selector.LabelSelectorConfig(multiple=True)),
            vol.Optional(CONF_CLASSES, default=classes): _chips(classes, COMMON_CLASSES),
        }), {"collapsed": False}),
        vol.Optional("trigger"): section(vol.Schema({
            vol.Optional(CONF_TRIGGERS, default=clean_triggers(d.get(CONF_TRIGGERS))): selector.ObjectSelector(selector.ObjectSelectorConfig(
                multiple=True, label_field="value", description_field="for", fields=TRIGGER_FIELDS)),
        }), {"collapsed": False}),
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
        vol.Optional("advanced"): section(vol.Schema({
            vol.Optional(CONF_PROBLEM, default=True if problem is None else bool(problem)): selector.BooleanSelector(),
            vol.Optional(CONF_FILTER_YAML, default=str(d.get(CONF_FILTER_YAML) or "")): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_FOR, default=str(d.get(CONF_FOR) or "")): selector.TextSelector(),
        }), {"collapsed": not has_yaml}),
    })


def _clean(flat: dict[str, Any]) -> dict[str, Any]:
    """A flattened submission → rule options, before validation (no triggers yet: see _absorb_yaml)."""
    out: dict[str, Any] = {
        CONF_NAME: str(flat.get(CONF_NAME) or "").strip(),
        # chip entries may still carry commas (a client posting one string)
        CONF_PATTERNS: [p for item in split_list(flat.get(CONF_PATTERNS)) for p in split_list(item)],
        CONF_LABELS: [s for s in (flat.get(CONF_LABELS) or []) if s and not str(s).startswith("___")],
        CONF_AREAS: [s for s in (flat.get(CONF_AREAS) or []) if s and not str(s).startswith("___")],
        CONF_CLASSES: [c for c in split_list(flat.get(CONF_CLASSES)) if c.strip(": ")],
    }
    for k in ADVANCED_KEYS:
        v = flat.get(k)
        out[k] = (True if v is None else bool(v)) if k == CONF_PROBLEM else str(v or "").strip()
    out.update({k: flat.get(k) for k in (*ACTION_KEYS, *EFFECT_KEYS)})
    return out


def _absorb_yaml(opts: dict[str, Any], posted_triggers: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Settle the triggers. No YAML: the posted rows. YAML the model can express: its
    selection and rows REPLACE the form's and the YAML is emptied. Other YAML: it defines
    the filter, the rows are dropped."""
    if not opts[CONF_FILTER_YAML]:
        return {**opts, CONF_FOR: "", CONF_TRIGGERS: clean_triggers(posted_triggers)}, {}
    try:
        cfg = _yaml_filter(opts)
    except Exception:  # noqa: BLE001
        return {**opts, CONF_TRIGGERS: []}, {"base": "bad_yaml"}
    if opts[CONF_FOR] and duration_seconds(opts[CONF_FOR]) is None:
        return {**opts, CONF_TRIGGERS: []}, {"base": "bad_duration"}
    model = model_from_filter(cfg, opts[CONF_FOR])
    if model is None:
        return {**opts, CONF_TRIGGERS: []}, {}
    return {**opts, **model, CONF_FILTER_YAML: "", CONF_FOR: ""}, {}


def _live(hass, d: dict[str, Any]) -> dict[str, str]:
    """What the (saved or just-submitted) selection holds right now, for the description."""
    sel = selection_filter(d) if not str(d.get(CONF_FILTER_YAML) or "").strip() else {}
    if not sel:
        return {"selected": "", "vocab": ""}
    ids = selected_ids(hass, sel)
    vocab = [v for v in values_now(hass, ids) if v.get("current")]
    vocab.sort(key=lambda v: -v["current"])
    words = " · ".join(f"{v['label']} {v['current']}" for v in vocab[:12])
    n = len(ids)
    return {"selected": f"The selection holds {n} {'entity' if n == 1 else 'entities'} now.",
            "vocab": f" Word states among them: {words}." if words else ""}


def _validate(hass, options: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Errors and description placeholders for the rule about to be saved."""
    errors: dict[str, str] = {}
    ph: dict[str, str] = {"unmatched": ""}
    if not options[CONF_NAME]:
        return {CONF_NAME: "no_name"}, ph
    problems = trigger_errors(options.get(CONF_TRIGGERS) or [])
    if problems:
        ph["unmatched"] = "; ".join(problems)
        return {"base": "bad_trigger"}, ph
    try:
        clauses = build_clauses(options)
        selection = rule_selection(options)
    except Exception as err:  # noqa: BLE001
        ph["unmatched"] = str(err)
        return {"base": "bad_yaml"}, ph
    sel = parse_filter(selection)
    if not sel.configured:
        return {"base": "empty_filter"}, ph          # a rule needs a selection: SB Filter never selects "everything"
    unreadable = list(sel.unreadable) + [u for c in clauses for u in parse_condition(c.condition).unreadable]
    if unreadable:
        ph["unmatched"] = "; ".join(unreadable)
        return {"base": "bad_trigger"}, ph
    ids = selected_ids(hass, selection)
    unmatched = [u for c in clauses for u in unmatched_now(hass, c.condition, ids)]
    if unmatched:
        errors["base"] = "unknown_value"
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


class _OneStep:
    """Shared by the config and options flows."""

    _defaults: dict[str, Any] = {}

    async def _do_step(self, step_id: str, user_input: dict[str, Any] | None):
        errors: dict[str, str] = {}
        ph = {"unmatched": ""}
        shown = self._defaults
        if user_input is not None:
            flat = _flatten(user_input)
            options, errors = _absorb_yaml(_clean(flat), flat.get(CONF_TRIGGERS))
            if not errors:
                errors, ph = _validate(self.hass, options)
            if not errors:
                return self._finish(options)
            shown = {**flat, CONF_TRIGGERS: clean_triggers(flat.get(CONF_TRIGGERS))}   # what was typed, not what is stored
        return self.async_show_form(step_id=step_id, data_schema=_schema(shown), errors=errors,
                                    description_placeholders={**_live(self.hass, shown), **ph, "editor": self._editor_link()})


class SbWatchConfigFlow(_OneStep, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = OPTIONS_VERSION

    def __init__(self) -> None:
        self._defaults = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._do_step("user", user_input)

    def _editor_link(self) -> str:
        return "[Open the SB Watch editor](/sb-watch?add=1) to build the rule with inline trigger rows and live counts, or fill this form."

    def _finish(self, options: dict[str, Any]):
        return self.async_create_entry(title=options[CONF_NAME], data={}, options=options)

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry):
        return SbWatchOptionsFlow()


class SbWatchOptionsFlow(_OneStep, config_entries.OptionsFlow):
    def __init__(self) -> None:
        self._defaults = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if not self._defaults:
            self._defaults = upgrade_options(dict(self.config_entry.options))
        return await self._do_step("init", user_input)

    def _editor_link(self) -> str:
        return (f"[Open this rule in the SB Watch editor](/sb-watch?edit={self.config_entry.entry_id}) "
                "for inline trigger rows and live counts, or edit it here.")

    def _finish(self, options: dict[str, Any]):
        self.hass.config_entries.async_update_entry(self.config_entry, title=options[CONF_NAME])
        return self.async_create_entry(title="", data=options)
