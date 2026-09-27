"""SB Watch — rules that watch what an SB filter matches.

A rule = an SB Filter config + a dwell (`for`). Each rule is a config entry
with a device page: a binary sensor (anything active), a count sensor with
the active entity ids, and the `sb_watch_changed` event on every change.
Actions come later and hang off that event.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .runner import RuleRunner

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    hass.data.setdefault(DOMAIN, {})["version"] = str(integration.version) if integration.version else None

    async def refresh(call: ServiceCall) -> None:
        for runner in hass.data[DOMAIN].get("runners", {}).values():
            runner.stop()
            runner.start()

    if not hass.services.has_service(DOMAIN, "refresh"):
        hass.services.async_register(DOMAIN, "refresh", refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runner = RuleRunner(hass, entry)
    hass.data.setdefault(DOMAIN, {}).setdefault("runners", {})[entry.entry_id] = runner
    entry.runtime_data = runner
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    runner.start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The rule is being deleted (not reloaded): take its notification down with it."""
    from .actions import RuleActions  # noqa: PLC0415

    RuleActions(hass, entry.entry_id, entry.title, entry.options).clear_notification()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runner: RuleRunner = entry.runtime_data
    runner.stop()
    hass.data[DOMAIN].get("runners", {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
