"""Websocket API to interact with the floor registry."""

from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.floor_registry import FloorEntry
from homeassistant.helpers.json import json_bytes

from .registry_websocket import (
    REGISTRY_EVENT_ADD,
    REGISTRY_EVENT_CHANGE,
    REGISTRY_EVENT_INITIAL,
    REGISTRY_EVENT_ORDER,
    REGISTRY_EVENT_REMOVE,
    construct_registry_event_message,
    json_array_from_fragments,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_setup(hass: HomeAssistant) -> bool:
    """Register the floor registry WS commands."""
    websocket_api.async_register_command(hass, websocket_list_floors)
    websocket_api.async_register_command(hass, websocket_subscribe_floors)
    websocket_api.async_register_command(hass, websocket_create_floor)
    websocket_api.async_register_command(hass, websocket_delete_floor)
    websocket_api.async_register_command(hass, websocket_update_floor)
    websocket_api.async_register_command(hass, websocket_reorder_floors)
    return True


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/list",
    }
)
@callback
def websocket_list_floors(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle list floors command."""
    registry = fr.async_get(hass)
    connection.send_result(
        msg["id"],
        [_entry_dict(entry) for entry in registry.async_list_floors()],
    )


@callback
def _compressed_entry_dict(entry: FloorEntry) -> dict[str, Any]:
    """Convert entry to compact API format."""
    return {
        "al": list(entry.aliases),
        "cr": entry.created_at.timestamp(),
        "ic": entry.icon,
        "id": entry.floor_id,
        "lv": entry.level,
        "mo": entry.modified_at.timestamp(),
        "nm": entry.name,
    }


@callback
def _compressed_entry_json(entry: FloorEntry) -> bytes | None:
    """Convert entry to compact API JSON."""
    try:
        return json_bytes(_compressed_entry_dict(entry))
    except ValueError, TypeError:
        _LOGGER.exception(
            "Unable to serialize compact floor registry entry %s to JSON",
            entry.floor_id,
        )
    return None


@callback
def _forward_floor_registry_changes(
    connection: ActiveConnection,
    registry: fr.FloorRegistry,
    msg_id: int,
    event: Event[fr.EventFloorRegistryUpdatedData],
) -> None:
    """Forward floor registry updates to the websocket."""
    if event.data["action"] == "remove":
        connection.send_message(
            construct_registry_event_message(
                msg_id,
                (REGISTRY_EVENT_REMOVE, json_bytes([event.data["floor_id"]])),
            )
        )
        return

    if event.data["action"] == "reorder":
        connection.send_message(
            construct_registry_event_message(
                msg_id,
                (
                    REGISTRY_EVENT_ORDER,
                    json_bytes(
                        [entry.floor_id for entry in registry.async_list_floors()]
                    ),
                ),
            )
        )
        return

    if (entry := registry.async_get_floor(event.data["floor_id"])) is None:
        return

    if (entry_json := _compressed_entry_json(entry)) is None:
        return

    connection.send_message(
        construct_registry_event_message(
            msg_id,
            (
                REGISTRY_EVENT_ADD
                if event.data["action"] == "create"
                else REGISTRY_EVENT_CHANGE,
                json_array_from_fragments((entry_json,)),
            ),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/subscribe",
    }
)
@callback
def websocket_subscribe_floors(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle subscribe floors command."""
    registry = fr.async_get(hass)
    msg_id = msg["id"]
    connection.subscriptions[msg_id] = hass.bus.async_listen(
        fr.EVENT_FLOOR_REGISTRY_UPDATED,
        partial(_forward_floor_registry_changes, connection, registry, msg_id),
    )
    connection.send_result(msg_id)
    connection.send_message(
        construct_registry_event_message(
            msg_id,
            (
                REGISTRY_EVENT_INITIAL,
                json_array_from_fragments(
                    entry_json
                    for entry in registry.async_list_floors()
                    if (entry_json := _compressed_entry_json(entry)) is not None
                ),
            ),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/create",
        vol.Required("name"): str,
        vol.Optional("aliases"): list,
        vol.Optional("icon"): vol.Any(str, None),
        vol.Optional("level"): vol.Any(int, None),
    }
)
@websocket_api.require_admin
@callback
def websocket_create_floor(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create floor command."""
    registry = fr.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    if "aliases" in data:
        # Create a set for the aliases without:
        #   - Empty strings
        #   - Trailing and leading whitespace characters in the individual aliases
        data["aliases"] = {s_strip for s in data["aliases"] if (s_strip := s.strip())}

    try:
        entry = registry.async_create(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    else:
        connection.send_result(msg["id"], _entry_dict(entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/delete",
        vol.Required("floor_id"): str,
    }
)
@websocket_api.require_admin
@callback
def websocket_delete_floor(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete floor command."""
    registry = fr.async_get(hass)

    try:
        registry.async_delete(msg["floor_id"])
    except KeyError:
        connection.send_error(msg["id"], "invalid_info", "Floor ID doesn't exist")
    else:
        connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/update",
        vol.Required("floor_id"): str,
        vol.Optional("aliases"): list,
        vol.Optional("icon"): vol.Any(str, None),
        vol.Optional("level"): vol.Any(int, None),
        vol.Optional("name"): str,
    }
)
@websocket_api.require_admin
@callback
def websocket_update_floor(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle update floor websocket command."""
    registry = fr.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    if "aliases" in data:
        # Create a set for the aliases without:
        #   - Empty strings
        #   - Trailing and leading whitespace characters in the individual aliases
        data["aliases"] = {s_strip for s in data["aliases"] if (s_strip := s.strip())}

    try:
        entry = registry.async_update(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    else:
        connection.send_result(msg["id"], _entry_dict(entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/floor_registry/reorder",
        vol.Required("floor_ids"): [str],
    }
)
@websocket_api.require_admin
@callback
def websocket_reorder_floors(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle reorder floors websocket command."""
    registry = fr.async_get(hass)

    try:
        registry.async_reorder(msg["floor_ids"])
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    else:
        connection.send_result(msg["id"])


@callback
def _entry_dict(entry: FloorEntry) -> dict[str, Any]:
    """Convert entry to API format."""
    return {
        "aliases": list(entry.aliases),
        "created_at": entry.created_at.timestamp(),
        "floor_id": entry.floor_id,
        "icon": entry.icon,
        "level": entry.level,
        "name": entry.name,
        "modified_at": entry.modified_at.timestamp(),
    }
