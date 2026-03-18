"""HTTP views to interact with the device registry."""

from __future__ import annotations

from functools import partial
import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant import loader
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import require_admin
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceEntryDisabler
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
    """Enable the Device Registry views."""

    websocket_api.async_register_command(hass, websocket_list_devices)
    websocket_api.async_register_command(hass, websocket_subscribe_devices)
    websocket_api.async_register_command(hass, websocket_update_device)
    websocket_api.async_register_command(
        hass, websocket_remove_config_entry_from_device
    )
    return True


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/device_registry/list",
    }
)
def websocket_list_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle list devices command."""
    registry = dr.async_get(hass)
    # Build start of response message
    msg_json_prefix = (
        f'{{"id":{msg["id"]},"type": "{websocket_api.TYPE_RESULT}",'
        f'"success":true,"result": ['
    ).encode()
    # Concatenate cached entity registry item JSON serializations
    inner = b",".join(
        [
            entry.json_repr
            for entry in registry.devices.values()
            if entry.json_repr is not None
        ]
    )
    msg_json = b"".join((msg_json_prefix, inner, b"]}"))
    connection.send_message(msg_json)


@callback
def _device_entry_compressed_dict(entry: DeviceEntry) -> dict[str, Any]:
    """Return a compressed dict representation of a device entry."""
    return {
        "ai": entry.area_id,
        "ce": list(entry.config_entries),
        "co": list(entry.connections),
        "cr": entry.created_at.timestamp(),
        "cs": {
            config_entry_id: list(subentries)
            for config_entry_id, subentries in entry.config_entries_subentries.items()
        },
        "cu": entry.configuration_url,
        "db": entry.disabled_by,
        "et": entry.entry_type,
        "hw": entry.hw_version,
        "id": entry.id,
        "ii": list(entry.identifiers),
        "lb": list(entry.labels),
        "md": entry.model,
        "mf": entry.manufacturer,
        "mi": entry.model_id,
        "mo": entry.modified_at.timestamp(),
        "nb": entry.name_by_user,
        "nm": entry.name,
        "pc": entry.primary_config_entry,
        "sn": entry.serial_number,
        "sw": entry.sw_version,
        "vd": entry.via_device_id,
    }


@callback
def _device_entry_compressed_json(entry: DeviceEntry) -> bytes | None:
    """Return a compressed JSON representation of a device entry."""
    try:
        return json_bytes(_device_entry_compressed_dict(entry))
    except ValueError, TypeError:
        _LOGGER.exception(
            "Unable to serialize compact device registry entry %s to JSON", entry.id
        )
    return None


@callback
def _forward_device_registry_changes(
    connection: websocket_api.ActiveConnection,
    registry: dr.DeviceRegistry,
    msg_id: int,
    event: Event[dr.EventDeviceRegistryUpdatedData],
) -> None:
    """Forward device registry updates to the websocket."""
    if event.data["action"] == "remove":
        connection.send_message(
            construct_registry_event_message(
                msg_id,
                (REGISTRY_EVENT_REMOVE, json_bytes([event.data["device_id"]])),
            )
        )
        return

    if (entry := registry.async_get(event.data["device_id"])) is None:
        return

    if (entry_json := _device_entry_compressed_json(entry)) is None:
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
        vol.Required("type"): "config/device_registry/subscribe",
    }
)
@callback
def websocket_subscribe_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle subscribe devices command."""
    registry = dr.async_get(hass)
    msg_id = msg["id"]
    connection.subscriptions[msg_id] = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        partial(_forward_device_registry_changes, connection, registry, msg_id),
    )
    connection.send_result(msg_id)
    connection.send_message(
        construct_registry_event_message(
            msg_id,
            (
                REGISTRY_EVENT_INITIAL,
                json_array_from_fragments(
                    entry_json
                    for entry in registry.devices.values()
                    if (entry_json := _device_entry_compressed_json(entry)) is not None
                ),
            ),
        )
    )


@require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/device_registry/update",
        vol.Optional("area_id"): vol.Any(str, None),
        vol.Required("device_id"): str,
        # We only allow setting disabled_by user via API.
        # No Enum support like this in voluptuous, use .value
        vol.Optional("disabled_by"): vol.Any(DeviceEntryDisabler.USER.value, None),
        vol.Optional("labels"): [str],
        vol.Optional("name_by_user"): vol.Any(str, None),
    }
)
@callback
def websocket_update_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle update device websocket command."""
    registry = dr.async_get(hass)

    msg.pop("type")
    msg_id = msg.pop("id")

    if msg.get("disabled_by") is not None:
        msg["disabled_by"] = DeviceEntryDisabler(msg["disabled_by"])

    if "labels" in msg:
        # Convert labels to a set
        msg["labels"] = set(msg["labels"])

    entry = cast(DeviceEntry, registry.async_update_device(**msg))

    connection.send_message(websocket_api.result_message(msg_id, entry.dict_repr))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        "type": "config/device_registry/remove_config_entry",
        "config_entry_id": str,
        "device_id": str,
    }
)
@websocket_api.async_response
async def websocket_remove_config_entry_from_device(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove config entry from a device."""
    registry = dr.async_get(hass)
    config_entry_id = msg["config_entry_id"]
    device_id = msg["device_id"]

    if (config_entry := hass.config_entries.async_get_entry(config_entry_id)) is None:
        raise HomeAssistantError("Unknown config entry")

    if not config_entry.supports_remove_device:
        raise HomeAssistantError("Config entry does not support device removal")

    if (device_entry := registry.async_get(device_id)) is None:
        raise HomeAssistantError("Unknown device")

    if config_entry_id not in device_entry.config_entries:
        raise HomeAssistantError("Config entry not in device")

    try:
        integration = await loader.async_get_integration(hass, config_entry.domain)
        component = await integration.async_get_component()
    except (ImportError, loader.IntegrationNotFound) as exc:
        raise HomeAssistantError("Integration not found") from exc

    if not await component.async_remove_config_entry_device(
        hass, config_entry, device_entry
    ):
        raise HomeAssistantError(
            "Failed to remove device entry, rejected by integration"
        )

    # Integration might have removed the config entry already, that is fine.
    if registry.async_get(device_id):
        entry = registry.async_update_device(
            device_id, remove_config_entry_id=config_entry_id
        )

        entry_as_dict = entry.dict_repr if entry else None
    else:
        entry_as_dict = None

    connection.send_message(websocket_api.result_message(msg["id"], entry_as_dict))
