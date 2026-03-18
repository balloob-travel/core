"""Websocket API to interact with the category registry."""

from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import category_registry as cr, config_validation as cv
from homeassistant.helpers.json import json_bytes

from .registry_websocket import (
    REGISTRY_EVENT_ADD,
    REGISTRY_EVENT_CHANGE,
    REGISTRY_EVENT_INITIAL,
    REGISTRY_EVENT_REMOVE,
    construct_registry_event_message,
    json_array_from_fragments,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_setup(hass: HomeAssistant) -> bool:
    """Register the category registry WS commands."""
    websocket_api.async_register_command(hass, websocket_list_categories)
    websocket_api.async_register_command(hass, websocket_subscribe_categories)
    websocket_api.async_register_command(hass, websocket_create_category)
    websocket_api.async_register_command(hass, websocket_delete_category)
    websocket_api.async_register_command(hass, websocket_update_category)
    return True


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/category_registry/list",
        vol.Required("scope"): str,
    }
)
@callback
def websocket_list_categories(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle list categories command."""
    category_registry = cr.async_get(hass)
    connection.send_result(
        msg["id"],
        [
            _entry_dict(entry)
            for entry in category_registry.async_list_categories(scope=msg["scope"])
        ],
    )


@callback
def _compressed_entry_dict(entry: cr.CategoryEntry) -> dict[str, Any]:
    """Convert entry to compact API format."""
    return {
        "cr": entry.created_at.timestamp(),
        "ic": entry.icon,
        "id": entry.category_id,
        "mo": entry.modified_at.timestamp(),
        "nm": entry.name,
    }


@callback
def _compressed_entry_json(entry: cr.CategoryEntry) -> bytes | None:
    """Convert entry to compact API JSON."""
    try:
        return json_bytes(_compressed_entry_dict(entry))
    except ValueError, TypeError:
        _LOGGER.exception(
            "Unable to serialize compact category registry entry %s to JSON",
            entry.category_id,
        )
    return None


@callback
def _forward_category_registry_changes(
    connection: ActiveConnection,
    registry: cr.CategoryRegistry,
    msg_id: int,
    scope: str,
    event: Event[cr.EventCategoryRegistryUpdatedData],
) -> None:
    """Forward category registry updates to the websocket."""
    if event.data["scope"] != scope:
        return

    if event.data["action"] == "remove":
        connection.send_message(
            construct_registry_event_message(
                msg_id,
                (REGISTRY_EVENT_REMOVE, json_bytes([event.data["category_id"]])),
            )
        )
        return

    if (
        entry := registry.async_get_category(
            scope=scope, category_id=event.data["category_id"]
        )
    ) is None:
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
        vol.Required("type"): "config/category_registry/subscribe",
        vol.Required("scope"): str,
    }
)
@callback
def websocket_subscribe_categories(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle subscribe categories command."""
    category_registry = cr.async_get(hass)
    msg_id = msg["id"]
    scope = msg["scope"]
    connection.subscriptions[msg_id] = hass.bus.async_listen(
        cr.EVENT_CATEGORY_REGISTRY_UPDATED,
        partial(
            _forward_category_registry_changes,
            connection,
            category_registry,
            msg_id,
            scope,
        ),
    )
    connection.send_result(msg_id)
    connection.send_message(
        construct_registry_event_message(
            msg_id,
            (
                REGISTRY_EVENT_INITIAL,
                json_array_from_fragments(
                    entry_json
                    for entry in category_registry.async_list_categories(scope=scope)
                    if (entry_json := _compressed_entry_json(entry)) is not None
                ),
            ),
        )
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/category_registry/create",
        vol.Required("scope"): str,
        vol.Required("name"): str,
        vol.Optional("icon"): vol.Any(cv.icon, None),
    }
)
@websocket_api.require_admin
@callback
def websocket_create_category(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create category command."""
    category_registry = cr.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    try:
        entry = category_registry.async_create(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    else:
        connection.send_result(msg["id"], _entry_dict(entry))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/category_registry/delete",
        vol.Required("scope"): str,
        vol.Required("category_id"): str,
    }
)
@websocket_api.require_admin
@callback
def websocket_delete_category(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete category command."""
    category_registry = cr.async_get(hass)

    try:
        category_registry.async_delete(
            scope=msg["scope"], category_id=msg["category_id"]
        )
    except KeyError:
        connection.send_error(msg["id"], "invalid_info", "Category ID doesn't exist")
    else:
        connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/category_registry/update",
        vol.Required("scope"): str,
        vol.Required("category_id"): str,
        vol.Optional("name"): str,
        vol.Optional("icon"): vol.Any(cv.icon, None),
    }
)
@websocket_api.require_admin
@callback
def websocket_update_category(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Handle update category websocket command."""
    category_registry = cr.async_get(hass)

    data = dict(msg)
    data.pop("type")
    data.pop("id")

    try:
        entry = category_registry.async_update(**data)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_info", str(err))
    except KeyError:
        connection.send_error(msg["id"], "invalid_info", "Category ID doesn't exist")
    else:
        connection.send_result(msg["id"], _entry_dict(entry))


@callback
def _entry_dict(entry: cr.CategoryEntry) -> dict[str, Any]:
    """Convert entry to API format."""
    return {
        "category_id": entry.category_id,
        "created_at": entry.created_at.timestamp(),
        "icon": entry.icon,
        "modified_at": entry.modified_at.timestamp(),
        "name": entry.name,
    }
