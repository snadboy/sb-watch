"""WebSocket API for editors: the state side of a rule.

A SOURCE is {filter: <SB Filter entry id>} | {entities: [...]} | {selection: {...}}
(a bare `selection` param is accepted too, for older editors).

  sb_watch/values  {source}                  → the source's entities' states, with counts (chips, suggestions)
  sb_watch/preview {source, triggers}        → selected ids, ids meeting any trigger NOW (durations not applied),
                                               typo report
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .live import async_preview, selected_ids, values_now
from .rule import build_clauses, clean_triggers


def _source(msg: dict) -> dict:
    return msg.get("source") or ({"selection": msg["selection"]} if msg.get("selection") else {})


@websocket_api.websocket_command({vol.Required("type"): "sb_watch/values", vol.Optional("source"): dict, vol.Optional("selection"): dict})
@callback
def ws_values(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    connection.send_result(msg["id"], {"values": values_now(hass, selected_ids(hass, _source(msg)))})


@websocket_api.websocket_command({vol.Required("type"): "sb_watch/preview", vol.Optional("source"): dict, vol.Optional("selection"): dict,
                                  vol.Optional("triggers", default=[]): list})
@websocket_api.async_response
async def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    rows = clean_triggers(msg["triggers"])
    conditions = [c.condition for c in build_clauses({"triggers": rows})] if rows else []
    connection.send_result(msg["id"], await async_preview(hass, _source(msg), conditions))


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_values)
    websocket_api.async_register_command(hass, ws_preview)
