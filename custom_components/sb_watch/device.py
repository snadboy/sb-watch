"""One SERVICE device per rule; the entities carry only their own noun."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def device_info(entry: ConfigEntry, name: str, version: str | None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name=name,
        manufacturer="snadboy",
        model="SB Watch rule",
        sw_version=version,
    )
