"""Diagnostics: the rule as SB Filter sees it and what it currently holds."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    r = entry.runtime_data
    return {
        "options": dict(entry.options),
        "filter": r.filter_config,
        "dwell_seconds": r.dwell_seconds,
        "matched": list(r.matched),
        "active": list(r.active),
        "pending": [e for e in r.matched if e not in r.active],
        "last_payload": r.last_payload,
    }
