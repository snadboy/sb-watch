"""binary_sensor.<rule>_active: on while anything is active."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROBLEM, DOMAIN
from .device import device_info
from .runner import RuleRunner


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ActiveBinarySensor(hass, entry, entry.runtime_data)])


class ActiveBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Active"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runner: RuleRunner) -> None:
        self._runner = runner
        self._attr_unique_id = f"{entry.entry_id}_active"
        self._attr_device_info = device_info(entry, runner.name, hass.data[DOMAIN].get("version"))
        if entry.options.get(CONF_PROBLEM, True):
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runner.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self._runner.active)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "count": len(self._runner.active),
            "active_since": self._runner.state.active_since.isoformat() if self._runner.state.active_since else None,
        }
