"""HTTP views to interact with the area registry."""

from __future__ import annotations

from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
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
    """Enable the Area Registry views."""
    websocket_api.async_register_command(hass, websocket_list_areas)
    websocket_api.async_register_command(hass, websocket_subscribe_areas)
    websocket_api.async_register_command(hass, websocket_create_area)
    websocket_api.async_register_command(hass, websocket_delete_area)
    websocket_api.async_register_command(hass, websocket_update_area)
    websocket_api.async_register_command(hass, websocket_reorder_areas)
    return True


@websocket_api.websocket_command({vol.Required("type"): "config/area_registry/list"})
@callback
def websocket_list_areas(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle list areas command."""
    registry = ar.async_get(hass)
    connection.send_result(
        msg["id"],
        [entry.json_fragment for entry in registry.async_list_areas()],
    )


@callback
def _area_entry_compressed_dict(entry: ar.AreaEntry) -> dict[str, Any]:
    """Return a compressed dict representation of an area entry."""
    return {
        "al": list(entry.aliases),
        "cr": entry.created_at.timestamp(),
        "fi": entry.floor_id,
        "he": entry.humidity_entity_id,
        "ic": entry.icon,
        "id": entry.id,
        "lb": list(entry.labels),
        "mo": entry.modified_at.timestamp(),
        "nm": entry.name,
        "pc": entry.picture,
        "te": entry.temperature_entity_id,
    }


@callback
def _area_entry_compressed_json(entry: ar.AreaEntry) -> bytes | None:
    """Return a compressed JSON representation of an area entry."""
    try:
        return json_bytes(_area_entry_compressed_dict(entry))
    except ValueError, TypeError:
        _LOGGER.exception(
            "Unable to serialize compact area registry entry %s to JSON", entry.id
        )
    return None


@callback
def _forward_area_registry_changes(
    connection: websocket_api.ActiveConnection,
    registry: ar.AreaRegistry,
    msg_id: int,
    event: Event[ar.EventAreaRegistryUpdatedData],
) -> None:
    """Forward area registry updates to the websocket."""
    if event.data["action"] == "remove":
        if (area_id := event.data["area_id"]) is None:
            return
        connection.send_message(
            construct_registry_event_message(
                msg_id, (REGISTRY_EVENT_REMOVE, json_bytes([area_id]))
            )
        )
        return

    if event.data["action"] == "reorder":
        connection.send_message(
            construct_registry_event_message(
                msg_id,
                (
                    REGISTRY_EVENT_ORDER,
                    json_bytes([entry.id for entry in registry.async_list_areas()]),
                ),
            )
        )
        return

    if (area_id := event.data["area_id"]) is None:
        return

    if (entry := registry.async_get_area(area_id)) is None:
        return

    if (entry_json := _area_entry_compressed_json(entry)) is None:
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
    {vol.Required("type"): "config/area_registry/subscribe"}
)
@callback
def websocket_subscribe_areas(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle subscribe areas command."""
    registry = ar.async_get(hass)
    msg_id = msg["id"]
    connection.subscriptions[msg_id] = hass.bus.async_listen(
        ar.EVENT_AREA_REGISTRY_UPDATED,
        partial(_forward_area_registry_changes, connection, registry, msg_id),
    )
    connection.send_result(msg_id)
    connection.send_message(
        construct_registry_event_message(
            msg_id,
            (
                REGISTRY_EVENT_INITIAL,
                json_array_from_fragments(
                    entry_json
                    for entry in registry.async_list_areas()
                    if (entry_json := _area_entry_compressed_json(entry)) is not None
                ),
            ),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/area_registry/create",
        vol.Optional("aliases"): list,
        vol.Optional("floor_id"): str,
        vol.Optional("humidity_entity_id"): vol.Any(str, None),
        vol.Optional("icon"): str,
        vol.Optional("labels"): [str],
        vol.Required("name"): str,
        vol.Optional("picture"): vol.Any(str, None),
        vol.Optional("temperature_entity_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@callback
def websocket_create_area(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create area command."""
    registry = ar.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    if "aliases" in data:
        # Create a set for the aliases without:
        #   - Empty strings
        #   - Trailing and leading whitespace characters in the individual aliases
        data["aliases"] = {s_strip for s in data["aliases"] if (s_strip := s.strip())}

    if "labels" in data:
        # Convert labels to a set
        data["labels"] = set(data["labels"])

    try:
        entry = registry.async_create(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    else:
        connection.send_result(msg["id"], entry.json_fragment)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/area_registry/delete",
        vol.Required("area_id"): str,
    }
)
@websocket_api.require_admin
@callback
def websocket_delete_area(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete area command."""
    registry = ar.async_get(hass)

    try:
        registry.async_delete(msg["area_id"])
    except KeyError:
        connection.send_error(msg["id"], "invalid_info", "Area ID doesn't exist")
    else:
        connection.send_message(websocket_api.result_message(msg["id"], "success"))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/area_registry/update",
        vol.Optional("aliases"): list,
        vol.Required("area_id"): str,
        vol.Optional("floor_id"): vol.Any(str, None),
        vol.Optional("humidity_entity_id"): vol.Any(str, None),
        vol.Optional("icon"): vol.Any(str, None),
        vol.Optional("labels"): [str],
        vol.Optional("name"): str,
        vol.Optional("picture"): vol.Any(str, None),
        vol.Optional("temperature_entity_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@callback
def websocket_update_area(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle update area websocket command."""
    registry = ar.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    if "aliases" in data:
        # Create a set for the aliases without:
        #   - Empty strings
        #   - Trailing and leading whitespace characters in the individual aliases
        data["aliases"] = {s_strip for s in data["aliases"] if (s_strip := s.strip())}

    if "labels" in data:
        # Convert labels to a set
        data["labels"] = set(data["labels"])

    try:
        entry = registry.async_update(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    else:
        connection.send_result(msg["id"], entry.json_fragment)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/area_registry/reorder",
        vol.Required("area_ids"): [str],
    }
)
@websocket_api.require_admin
@callback
def websocket_reorder_areas(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle reorder areas websocket command."""
    registry = ar.async_get(hass)

    try:
        registry.async_reorder(msg["area_ids"])
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    else:
        connection.send_result(msg["id"])
