"""sensor.<rule>_count: how many are active, with the ids as attributes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import DOMAIN
from .device import device_info
from .runner import RuleRunner


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([CountSensor(hass, entry, entry.runtime_data)])


@dataclass
class DwellData(ExtraStoredData):
    """The runner's dwell clocks, stored beside the sensor's last state."""

    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.data


class CountSensor(SensorEntity, RestoreEntity):
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
        last = await self.async_get_last_extra_data()
        if last is not None:
            self._runner.restore(last.as_dict())
        self.async_on_remove(self._runner.add_listener(self._changed))

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return DwellData(self._runner.snapshot())

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
            "matched_since": {e: t.isoformat() for e, t in sorted(r.state.clocks().items())},   # the dwell clocks
            "filter": r.filter_config,          # one trigger: its whole filter; several: the selection they share
            "source": {**r.source, **({"filter_name": r.filter_name} if r.filter_name else {})},   # {filter} | {entities} | {selection}
            "selection": r.selection,            # an inline selection (YAML path / unmigrated); {} otherwise
            "triggers": r.triggers,              # [{kind, value, for, per}] — the rule as the form shows it
            "advanced": r.advanced,              # the YAML override defines the filter
            "for": r.dwell_seconds,
            "configured": r.configured,
            "unreadable": r.unreadable,
            "grammar": r.grammar,
            "in_effect": r.in_effect,
            "window": f"{r.effect.start[0]:02d}:{r.effect.start[1]:02d}-{r.effect.end[0]:02d}:{r.effect.end[1]:02d}" if r.effect.start else None,
            "days": sorted(r.effect.days, key=("mon", "tue", "wed", "thu", "fri", "sat", "sun").index) if r.effect.days else None,
            "action": r.actions.mode,
            "act": r.actions.act,
            "act_script": r.actions.act_script,
            "last_action": r.actions.last_action,
            "actions_taken": r.actions.actions_taken,
        }
