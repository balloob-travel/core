"""Helpers for compact registry websocket subscriptions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from logging import Logger
from typing import Any, TypeVar

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.messages import construct_event_message
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.json import json_bytes

REGISTRY_EVENT_INITIAL = "i"
REGISTRY_EVENT_UPDATE = "u"
REGISTRY_EVENT_REMOVE = "r"
REGISTRY_EVENT_ORDER = "o"

type RegistryEvent = Event[Any]
type RegistryEventPart = tuple[str, bytes]

EntryT = TypeVar("EntryT")


@callback
def _always_relevant(event: RegistryEvent) -> bool:
    """Return if a registry event should be forwarded."""
    return True


@callback
def _no_extra_initial_parts() -> tuple[()]:
    """Return extra parts for the initial subscription event."""
    return ()


@callback
def _no_extra_event_parts(event: RegistryEvent, entry: object | None) -> tuple[()]:
    """Return extra parts for a registry update event."""
    return ()


@dataclass(slots=True, kw_only=True)
class RegistrySubscriptionSpec[EntryT]:
    """Describe a compact registry websocket subscription."""

    event_type: str
    list_entries: Callable[[], Iterable[EntryT]]
    serialize_entry: Callable[[EntryT], bytes | None]
    get_entry: Callable[[RegistryEvent], EntryT | None]
    get_remove_id: Callable[[RegistryEvent], str | None]
    is_relevant: Callable[[RegistryEvent], bool] = _always_relevant
    get_order_payload: Callable[[RegistryEvent], bytes | None] | None = None
    extra_initial_parts: Callable[[], Iterable[RegistryEventPart]] = (
        _no_extra_initial_parts
    )
    extra_event_parts: Callable[
        [RegistryEvent, EntryT | None], Iterable[RegistryEventPart]
    ] = _no_extra_event_parts


def json_array_from_fragments(fragments: Iterable[bytes]) -> bytes:
    """Return a JSON array built from serialized JSON fragments."""
    return b"".join((b"[", b",".join(fragments), b"]"))


def construct_registry_event_message(msg_id: int, *parts: tuple[str, bytes]) -> bytes:
    """Construct a compact registry event message."""
    payload = b",".join(
        b"".join((b'"', key.encode(), b'":', value)) for key, value in parts
    )
    return construct_event_message(msg_id, b"".join((b"{", payload, b"}")))


@callback
def serialize_registry_entry(
    entry: EntryT,
    entry_to_dict: Callable[[EntryT], dict[str, Any]],
    logger: Logger,
    registry_name: str,
    entry_id: str,
) -> bytes | None:
    """Serialize a registry entry to compact JSON bytes."""
    try:
        return json_bytes(entry_to_dict(entry))
    except ValueError, TypeError:
        logger.exception(
            "Unable to serialize compact %s registry entry %s to JSON",
            registry_name,
            entry_id,
        )
    return None


@callback
def _forward_registry_event(
    connection: websocket_api.ActiveConnection,
    msg_id: int,
    subscription: RegistrySubscriptionSpec[EntryT],
    event: RegistryEvent,
) -> None:
    """Forward a registry event to the websocket."""
    if not subscription.is_relevant(event):
        return

    parts: list[RegistryEventPart] = []
    entry: EntryT | None = None
    action = event.data["action"]

    if action == "remove":
        if (remove_id := subscription.get_remove_id(event)) is not None:
            parts.append((REGISTRY_EVENT_REMOVE, json_bytes(remove_id)))
    elif action == "reorder":
        if (
            subscription.get_order_payload is not None
            and (order_payload := subscription.get_order_payload(event)) is not None
        ):
            parts.append((REGISTRY_EVENT_ORDER, order_payload))
    else:
        entry = subscription.get_entry(event)
        if (
            entry is not None
            and (entry_json := subscription.serialize_entry(entry)) is not None
        ):
            parts.append((REGISTRY_EVENT_UPDATE, entry_json))

    parts.extend(subscription.extra_event_parts(event, entry))

    if parts:
        connection.send_message(construct_registry_event_message(msg_id, *parts))


@callback
def async_subscribe_to_registry_updates(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    subscription: RegistrySubscriptionSpec[EntryT],
) -> None:
    """Subscribe to compact registry updates."""
    msg_id = msg["id"]
    connection.subscriptions[msg_id] = hass.bus.async_listen(
        subscription.event_type,
        partial(_forward_registry_event, connection, msg_id, subscription),
    )
    connection.send_result(msg_id)
    connection.send_message(
        construct_registry_event_message(
            msg_id,
            *subscription.extra_initial_parts(),
            (
                REGISTRY_EVENT_INITIAL,
                json_array_from_fragments(
                    entry_json
                    for entry in subscription.list_entries()
                    if (entry_json := subscription.serialize_entry(entry)) is not None
                ),
            ),
        )
    )
