"""HTTP views to interact with the entity registry."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ERR_NOT_FOUND, require_admin
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.json import json_bytes, json_dumps

from .registry_websocket import (
    REGISTRY_EVENT_REMOVE,
    RegistrySubscriptionSpec,
    async_subscribe_to_registry_updates,
    serialize_registry_entry,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_setup(hass: HomeAssistant) -> bool:
    """Enable the Entity Registry views."""

    websocket_api.async_register_command(hass, websocket_get_automatic_entity_ids)
    websocket_api.async_register_command(hass, websocket_get_entities)
    websocket_api.async_register_command(hass, websocket_get_entity)
    websocket_api.async_register_command(hass, websocket_list_entities_for_display)
    websocket_api.async_register_command(hass, websocket_list_entities)
    websocket_api.async_register_command(hass, websocket_subscribe_entities)
    websocket_api.async_register_command(hass, websocket_subscribe_entities_for_display)
    websocket_api.async_register_command(hass, websocket_remove_entity)
    websocket_api.async_register_command(hass, websocket_update_entity)
    return True


@websocket_api.websocket_command({vol.Required("type"): "config/entity_registry/list"})
@callback
def websocket_list_entities(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle list registry entries command."""
    registry = er.async_get(hass)
    # Build start of response message
    msg_json_prefix = (
        f'{{"id":{msg["id"]},"type": "{websocket_api.TYPE_RESULT}",'
        '"success":true,"result": ['
    ).encode()
    # Concatenate cached entity registry item JSON serializations
    inner = b",".join(
        [
            entry.partial_json_repr
            for entry in registry.entities.values()
            if entry.partial_json_repr is not None
        ]
    )
    msg_json = b"".join((msg_json_prefix, inner, b"]}"))
    connection.send_message(msg_json)


_ENTITY_CATEGORIES_JSON = json_dumps(er.ENTITY_CATEGORY_INDEX_TO_VALUE)
_ENTITY_CATEGORIES_BYTES = _ENTITY_CATEGORIES_JSON.encode()


@websocket_api.websocket_command(
    {vol.Required("type"): "config/entity_registry/list_for_display"}
)
@callback
def websocket_list_entities_for_display(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle list registry entries command."""
    registry = er.async_get(hass)
    # Build start of response message
    msg_json_prefix = (
        f'{{"id":{msg["id"]},"type":"{websocket_api.TYPE_RESULT}","success":true,'
        f'"result":{{"entity_categories":{_ENTITY_CATEGORIES_JSON},"entities":['
    ).encode()
    # Concatenate cached entity registry item JSON serializations
    inner = b",".join(
        [
            entry.display_json_repr
            for entry in registry.entities.values()
            if entry.disabled_by is None and entry.display_json_repr is not None
        ]
    )
    msg_json = b"".join((msg_json_prefix, inner, b"]}}"))
    connection.send_message(msg_json)


@callback
def _compressed_entity_dict(entry: er.RegistryEntry) -> dict[str, Any]:
    """Return a compressed dict representation of an entity registry entry."""
    return {
        "ai": entry.area_id,
        "ce": entry.config_entry_id,
        "cg": entry.categories,
        "cr": entry.created_at.timestamp(),
        "cs": entry.config_subentry_id,
        "db": entry.disabled_by,
        "di": entry.device_id,
        "ec": entry.entity_category,
        "ei": entry.entity_id,
        "hb": entry.hidden_by,
        "hn": entry.has_entity_name,
        "ic": entry.icon,
        "id": entry.id,
        "lb": list(entry.labels),
        "mo": entry.modified_at.timestamp(),
        "nm": entry.name,
        "on": entry.original_name,
        "op": entry.options,
        "pl": entry.platform,
        "tk": entry.translation_key,
        "ui": entry.unique_id,
    }


@callback
def _compressed_entity_json(entry: er.RegistryEntry) -> bytes | None:
    """Return a compressed JSON representation of an entity registry entry."""
    return serialize_registry_entry(
        entry, _compressed_entity_dict, _LOGGER, "entity", entry.entity_id
    )


@callback
def _entity_registry_extra_event_parts(
    event: Event[er.EventEntityRegistryUpdatedData], entry: er.RegistryEntry | None
) -> tuple[tuple[str, bytes], ...]:
    """Build extra entity registry websocket event parts."""
    if old_entity_id := event.data.get("old_entity_id"):
        return ((REGISTRY_EVENT_REMOVE, json_bytes(old_entity_id)),)
    return ()


@callback
def _display_entity_json(entry: er.RegistryEntry) -> bytes | None:
    """Return a display entry JSON representation if visible."""
    if entry.disabled_by is not None:
        return None
    return entry.display_json_repr


@callback
def _display_entity_registry_extra_event_parts(
    event: Event[er.EventEntityRegistryUpdatedData], entry: er.RegistryEntry | None
) -> tuple[tuple[str, bytes], ...]:
    """Build extra display entity registry websocket event parts."""
    old_entity_id = event.data.get("old_entity_id")

    if (
        entry is not None
        and entry.disabled_by is None
        and entry.display_json_repr is not None
    ):
        if (
            event.data["action"] == "update"
            and old_entity_id is not None
            and old_entity_id != entry.entity_id
        ):
            return ((REGISTRY_EVENT_REMOVE, json_bytes(old_entity_id)),)
    elif entry is None or (
        event.data["action"] == "update"
        and ("disabled_by" in event.data["changes"] or "old_entity_id" in event.data)
    ):
        return (
            (
                REGISTRY_EVENT_REMOVE,
                json_bytes(old_entity_id or event.data["entity_id"]),
            ),
        )

    return ()


@websocket_api.websocket_command(
    {vol.Required("type"): "config/entity_registry/subscribe"}
)
@callback
def websocket_subscribe_entities(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle subscribe entity registry command."""
    registry = er.async_get(hass)
    async_subscribe_to_registry_updates(
        hass,
        connection,
        msg,
        RegistrySubscriptionSpec(
            event_type=er.EVENT_ENTITY_REGISTRY_UPDATED,
            list_entries=registry.entities.values,
            serialize_entry=_compressed_entity_json,
            get_entry=lambda event: registry.async_get(event.data["entity_id"]),
            get_remove_id=lambda event: event.data["entity_id"],
            extra_event_parts=_entity_registry_extra_event_parts,
        ),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "config/entity_registry/subscribe_for_display"}
)
@callback
def websocket_subscribe_entities_for_display(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle subscribe entity registry display command."""
    registry = er.async_get(hass)
    async_subscribe_to_registry_updates(
        hass,
        connection,
        msg,
        RegistrySubscriptionSpec(
            event_type=er.EVENT_ENTITY_REGISTRY_UPDATED,
            list_entries=registry.entities.values,
            serialize_entry=_display_entity_json,
            get_entry=lambda event: registry.async_get(event.data["entity_id"]),
            get_remove_id=lambda event: event.data["entity_id"],
            extra_initial_parts=lambda: (("ec", _ENTITY_CATEGORIES_BYTES),),
            extra_event_parts=_display_entity_registry_extra_event_parts,
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/entity_registry/get",
        vol.Required("entity_id"): cv.entity_id,
    }
)
@callback
def websocket_get_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle get entity registry entry command.

    Async friendly.
    """
    registry = er.async_get(hass)

    if (entry := registry.entities.get(msg["entity_id"])) is None:
        connection.send_message(
            websocket_api.error_message(msg["id"], ERR_NOT_FOUND, "Entity not found")
        )
        return

    connection.send_message(
        websocket_api.result_message(msg["id"], entry.extended_dict)
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/entity_registry/get_entries",
        vol.Required("entity_ids"): cv.entity_ids,
    }
)
@callback
def websocket_get_entities(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle get entity registry entries command.

    Async friendly.
    """
    registry = er.async_get(hass)

    entity_ids = msg["entity_ids"]
    entries: dict[str, dict[str, Any] | None] = {}
    for entity_id in entity_ids:
        entry = registry.entities.get(entity_id)
        entries[entity_id] = entry.extended_dict if entry else None

    connection.send_message(websocket_api.result_message(msg["id"], entries))


@require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/entity_registry/update",
        vol.Required("entity_id"): cv.entity_id,
        # If passed in, we update value. Passing None will remove old value.
        vol.Optional("aliases"): list,
        vol.Optional("area_id"): vol.Any(str, None),
        # Categories is a mapping of key/value (scope/category_id) pairs.
        # If passed in, we update/adjust only the provided scope(s).
        # Other category scopes in the entity, are left as is.
        #
        # Categorized items such as entities
        # can only be in 1 category ID per scope at a time.
        # Therefore, passing in a category ID will either add or move
        # the entity to that specific category. Passing in None will
        # remove the entity from the category.
        vol.Optional("categories"): cv.schema_with_slug_keys(vol.Any(str, None)),
        vol.Optional("device_class"): vol.Any(str, None),
        vol.Optional("icon"): vol.Any(str, None),
        vol.Optional("labels"): [str],
        vol.Optional("name"): vol.Any(str, None),
        vol.Optional("new_entity_id"): str,
        # We only allow setting disabled_by user via API.
        vol.Optional("disabled_by"): vol.Any(
            None,
            vol.All(
                vol.Coerce(er.RegistryEntryDisabler),
                er.RegistryEntryDisabler.USER.value,
            ),
        ),
        # We only allow setting hidden_by user via API.
        vol.Optional("hidden_by"): vol.Any(
            None,
            vol.All(
                vol.Coerce(er.RegistryEntryHider),
                er.RegistryEntryHider.USER.value,
            ),
        ),
        vol.Inclusive("options_domain", "entity_option"): str,
        vol.Inclusive("options", "entity_option"): vol.Any(None, dict),
    }
)
@callback
def websocket_update_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle update entity websocket command.

    Async friendly.
    """
    registry = er.async_get(hass)

    entity_id = msg["entity_id"]
    if not (entity_entry := registry.async_get(entity_id)):
        connection.send_message(
            websocket_api.error_message(msg["id"], ERR_NOT_FOUND, "Entity not found")
        )
        return

    changes = {}

    for key in (
        "area_id",
        "device_class",
        "disabled_by",
        "hidden_by",
        "icon",
        "name",
        "new_entity_id",
    ):
        if key in msg:
            changes[key] = msg[key]

    if "aliases" in msg:
        # Create a set for the aliases without:
        #   - Empty strings
        #   - Trailing and leading whitespace characters in the individual aliases
        changes["aliases"] = {s_strip for s in msg["aliases"] if (s_strip := s.strip())}

    if "labels" in msg:
        # Convert labels to a set
        changes["labels"] = set(msg["labels"])

    if "disabled_by" in msg and msg["disabled_by"] is None:
        # Don't allow enabling an entity of a disabled device
        if entity_entry.device_id:
            device_registry = dr.async_get(hass)
            device = device_registry.async_get(entity_entry.device_id)
            if device and device.disabled:
                connection.send_message(
                    websocket_api.error_message(
                        msg["id"], "invalid_info", "Device is disabled"
                    )
                )
                return

    # Update the categories if provided
    if "categories" in msg:
        categories = entity_entry.categories.copy()
        for scope, category_id in msg["categories"].items():
            if scope in categories and category_id is None:
                # Remove the category from the scope as it was unset
                del categories[scope]
            elif category_id is not None:
                # Add or update the category for the given scope
                categories[scope] = category_id
        changes["categories"] = categories

    try:
        if changes:
            entity_entry = registry.async_update_entity(entity_id, **changes)
    except ValueError as err:
        connection.send_message(
            websocket_api.error_message(msg["id"], "invalid_info", str(err))
        )
        return

    if "new_entity_id" in msg:
        entity_id = msg["new_entity_id"]

    try:
        if "options_domain" in msg:
            entity_entry = registry.async_update_entity_options(
                entity_id, msg["options_domain"], msg["options"]
            )
    except ValueError as err:
        connection.send_message(
            websocket_api.error_message(msg["id"], "invalid_info", str(err))
        )
        return

    result: dict[str, Any] = {"entity_entry": entity_entry.extended_dict}
    if "disabled_by" in changes and changes["disabled_by"] is None:
        # Enabling an entity requires a config entry reload, or HA restart
        if not (config_entry_id := entity_entry.config_entry_id) or (
            (config_entry := hass.config_entries.async_get_entry(config_entry_id))
            and not config_entry.supports_unload
        ):
            result["require_restart"] = True
        else:
            result["reload_delay"] = config_entries.RELOAD_AFTER_UPDATE_DELAY
    connection.send_result(msg["id"], result)


@require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/entity_registry/remove",
        vol.Required("entity_id"): cv.entity_id,
    }
)
@callback
def websocket_remove_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle remove entity websocket command.

    Async friendly.
    """
    registry = er.async_get(hass)

    if msg["entity_id"] not in registry.entities:
        connection.send_message(
            websocket_api.error_message(msg["id"], ERR_NOT_FOUND, "Entity not found")
        )
        return

    registry.async_remove(msg["entity_id"])
    connection.send_message(websocket_api.result_message(msg["id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "config/entity_registry/get_automatic_entity_ids",
        vol.Required("entity_ids"): cv.entity_ids,
    }
)
@callback
def websocket_get_automatic_entity_ids(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the automatic entity IDs for the given entity IDs.

    This is used to help user reset entity IDs which have been customized by the user.
    """
    registry = er.async_get(hass)

    entity_ids = msg["entity_ids"]
    automatic_entity_ids: dict[str, str | None] = {}
    reserved_entity_ids: set[str] = set()
    for entity_id in entity_ids:
        if not (entry := registry.entities.get(entity_id)):
            automatic_entity_ids[entity_id] = None
            continue
        new_entity_id = registry.async_regenerate_entity_id(
            entry,
            reserved_entity_ids=reserved_entity_ids,
        )
        automatic_entity_ids[entity_id] = new_entity_id
        reserved_entity_ids.add(new_entity_id)

    connection.send_message(
        websocket_api.result_message(msg["id"], automatic_entity_ids)
    )
