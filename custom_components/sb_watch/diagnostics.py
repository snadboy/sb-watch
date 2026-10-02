"""Diagnostics: the rule's selection (SB Filter's answer), its clauses and what they hold."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    r = entry.runtime_data
    return {
        "options": dict(entry.options),
        "source": r.source,
        "filter_name": r.filter_name,
        "selection": r.selection_config,
        "selected": list(r.selected or ()),
        "filter": r.filter_config,
        "clauses": [{"key": c.key, "condition": c.condition, "dwell_seconds": c.dwell_seconds,
                     "matched": list(r.matched_by.get(c.key) or ()),
                     "active": list(r.state.states[c.key].active)} for c in r.clauses],
        "dwell_seconds": r.dwell_seconds,
        "matched": list(r.matched),
        "active": list(r.active),
        "pending": [e for e in r.matched if e not in r.active],
        "unreadable": r.unreadable,
        "grammar": r.grammar,
        "actions": {"mode": r.actions.mode, "act": r.actions.act, "warn_seconds": r.actions.warn_seconds,
                    "notify_service": r.actions.notify_service, "paused": r.actions.paused, "last_action": r.actions.last_action},
    }
