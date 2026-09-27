"""switch.<rule>_paused: the override. On = the rule keeps tracking but takes no
action (no notification, no act). Restored across restarts."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .device import device_info
from .runner import RuleRunner


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([PausedSwitch(hass, entry, entry.runtime_data)])


class PausedSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Paused"
    _attr_icon = "mdi:pause-circle-outline"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runner: RuleRunner) -> None:
        self._runner = runner
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_paused"
        self._attr_device_info = device_info(entry, runner.name, hass.data[DOMAIN].get("version"))
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        # Restore is keyed by ENTITY ID: a rule deleted and re-created under the
        # same name would inherit the old switch's "on" and silently take no
        # action. Only trust a restored state written by THIS config entry.
        last = await self.async_get_last_state()
        mine = bool(last and last.attributes.get("entry_id") == self._entry_id)
        self._attr_is_on = bool(mine and last.state == "on")
        self._runner.actions.paused = self._attr_is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {"entry_id": self._entry_id}

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self._runner.actions.paused = True
        self._runner.actions.stop()          # drop any warn-ahead timers
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self._runner.actions.paused = False
        self.async_write_ha_state()
