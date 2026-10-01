"""Diagnostics: the rule as SB Filter sees it and what it currently holds."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    r = entry.runtime_data
    return {
        "options": dict(entry.options),
        "filter": r.filter_config,
        "clauses": [{"key": c.key, "filter": c.filter, "dwell_seconds": c.dwell_seconds,
                     "matched": list((r.last_payload.get(c.key) or {}).get("ids") or ()),
                     "active": list(r.state.states[c.key].active)} for c in r.clauses],
        "dwell_seconds": r.dwell_seconds,
        "matched": list(r.matched),
        "active": list(r.active),
        "pending": [e for e in r.matched if e not in r.active],
        "last_payload": r.last_payload,
        "actions": {"mode": r.actions.mode, "act": r.actions.act, "warn_seconds": r.actions.warn_seconds,
                    "notify_service": r.actions.notify_service, "paused": r.actions.paused, "last_action": r.actions.last_action},
    }
