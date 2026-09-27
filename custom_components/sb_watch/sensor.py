"""sensor.<rule>_count: how many are active, with the ids as attributes."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import device_info
from .runner import RuleRunner


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([CountSensor(hass, entry, entry.runtime_data)])


class CountSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Count"
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:filter-check-outline"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runner: RuleRunner) -> None:
        self._runner = runner
        self._attr_unique_id = f"{entry.entry_id}_count"
        self._attr_device_info = device_info(entry, runner.name, hass.data[DOMAIN].get("version"))

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runner.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._runner.active)

    @property
    def extra_state_attributes(self) -> dict:
        r = self._runner
        return {
            "entity_ids": list(r.active),
            "names": r.names(),
            "matched": len(r.matched),          # before the dwell
            "pending": [e for e in r.matched if e not in r.active],
            "filter": r.filter_config,
            "for": r.dwell_seconds,
            "configured": r.configured,
            "unreadable": r.unreadable,
            "grammar": r.grammar,
        }
