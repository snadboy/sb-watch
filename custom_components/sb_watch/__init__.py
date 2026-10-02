"""SB Watch — rules that watch the state of the entities SB Filter selects, and act.

A rule = a selection (which entities — SB Filter) + triggers (state / range /
rate, each with its own duration — evaluated here) + actions. Each rule is a
config entry with a device page: a binary sensor (anything active), a count
sensor with the active entity ids, and the `sb_watch_changed` event on every change.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, HassJob, HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from homeassistant.exceptions import ConfigEntryNotReady

from homeassistant.helpers import entity_registry as er

from custom_components.sb_filter.const import GRAMMAR_VERSION as FILTER_GRAMMAR
from custom_components.sb_filter.named import async_find_or_create

from .const import DOMAIN, MIN_FILTER_GRAMMAR, OPTIONS_VERSION, STARTUP_SETTLE_SECONDS
from .rule import migration_target, upgrade_options
from . import websocket
from .runner import RuleRunner

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]
_LOGGER = logging.getLogger(__name__)
PANEL_URL = "sb-watch"                 # the sidebar page: /sb-watch (also ?edit=<entry_id>, ?add=1)
STATIC_URL = "/sb_watch_static"


async def _async_register_panel(hass: HomeAssistant, version: str | None) -> None:
    """The SB Watch sidebar panel: every rule, with the full rule editor (admins only).
    The JS is built from the SB Watch Card's source by tools/build_panel.py."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)])
    if PANEL_URL in hass.data.get("frontend_panels", {}):
        return
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_URL, webcomponent_name="sb-watch-panel", sidebar_title="SB Watch",
        sidebar_icon="mdi:filter-check-outline", module_url=f"{STATIC_URL}/sb-watch-panel.js?v={version or 0}",
        embed_iframe=False, require_admin=True)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    hass.data.setdefault(DOMAIN, {})["version"] = str(integration.version) if integration.version else None

    async def refresh(call: ServiceCall) -> None:
        for runner in hass.data[DOMAIN].get("runners", {}).values():
            runner.stop()
            runner.start()

    if not hass.services.has_service(DOMAIN, "refresh"):
        hass.services.async_register(DOMAIN, "refresh", refresh)
    websocket.async_register(hass)
    try:
        await _async_register_panel(hass, hass.data[DOMAIN]["version"])
    except Exception:  # noqa: BLE001 — the panel is a convenience; the rules must load without it
        _LOGGER.exception("SB Watch: could not register the sidebar panel")
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (one filter + one dwell) → v2 (selection + triggers): what the model cannot
    express stays as the advanced YAML. v2 → v3: the rule's own selection becomes a
    NAMED SB Filter (an existing one with exactly that selection, else a new one named
    after the rule), or — one pattern that is exactly an entity id — `entities`.
    No rule changes what it matches."""
    if entry.version > OPTIONS_VERSION:
        return False
    opts = dict(entry.options)
    if entry.version < 2:
        opts = upgrade_options(opts)
    if entry.version < 3:
        reg = er.async_get(hass)
        target = migration_target(opts, lambda e: reg.async_get(e) is not None or hass.states.get(e) is not None)
        if target is not None:
            for k in ("patterns", "labels", "areas", "classes"):
                opts.pop(k, None)
            if "entities" in target:
                opts.update({"entities": target["entities"], "filter": ""})
            else:
                try:
                    fid = await async_find_or_create(hass, opts.get("name") or entry.title, target["selection"])
                except Exception:  # noqa: BLE001 — SB Filter not ready: try again at the next start
                    _LOGGER.exception("SB Watch: could not make a filter for rule %s", entry.title)
                    return False
                opts.update({"filter": fid, "entities": []})
    if entry.version < OPTIONS_VERSION:
        hass.config_entries.async_update_entry(entry, options=opts, version=OPTIONS_VERSION)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if FILTER_GRAMMAR < MIN_FILTER_GRAMMAR:
        raise ConfigEntryNotReady(f"SB Filter 0.7.0+ needed (grammar {MIN_FILTER_GRAMMAR}, named filters); installed grammar is {FILTER_GRAMMAR} — update SB Filter")
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
        fired = False

        @callback
        def _settled(_now) -> None:
            runner.start()

        @callback
        def _started(_event) -> None:
            nonlocal fired
            fired = True
            entry.async_on_unload(async_call_later(hass, STARTUP_SETTLE_SECONDS, HassJob(_settled, cancel_on_shutdown=True)))

        unsub_started = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
        # a one-time listener removes itself when it fires; unsubscribing it again logs an error
        entry.async_on_unload(lambda: None if fired else unsub_started())
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
