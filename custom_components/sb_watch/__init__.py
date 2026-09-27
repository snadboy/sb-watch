"""SB Watch — rules that watch what an SB filter matches.

A rule = an SB Filter config + a dwell (`for`). Each rule is a config entry
with a device page: a binary sensor (anything active), a count sensor with
the active entity ids, and the `sb_watch_changed` event on every change.
Actions come later and hang off that event.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, HassJob, HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, STARTUP_SETTLE_SECONDS
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

    # BOOT RACE: right after a restart many entities still read unknown /
    # unavailable while their integrations come up. Evaluating then drops them
    # out of the match set and resets their restored dwell clocks (seen live:
    # 5 of 13 batteries restored, 8 back to pending). So on a cold start the
    # first evaluation waits for HA to be fully started plus a settle period;
    # the entities show the restored state meanwhile.
    if hass.state is CoreState.running:
        runner.start()
    else:
        @callback
        def _settled(_now) -> None:
            runner.start()

        @callback
        def _started(_event) -> None:
            entry.async_on_unload(async_call_later(hass, STARTUP_SETTLE_SECONDS, HassJob(_settled, cancel_on_shutdown=True)))

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started))
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
