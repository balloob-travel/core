"""Helpers for compact registry websocket subscriptions."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.websocket_api.messages import construct_event_message

REGISTRY_EVENT_INITIAL = "i"
REGISTRY_EVENT_ADD = "a"
REGISTRY_EVENT_CHANGE = "c"
REGISTRY_EVENT_REMOVE = "r"
REGISTRY_EVENT_ORDER = "o"


def json_array_from_fragments(fragments: Iterable[bytes]) -> bytes:
    """Return a JSON array built from serialized JSON fragments."""
    return b"".join((b"[", b",".join(fragments), b"]"))


def construct_registry_event_message(msg_id: int, *parts: tuple[str, bytes]) -> bytes:
    """Construct a compact registry event message."""
    payload = b",".join(
        b"".join((b'"', key.encode(), b'":', value)) for key, value in parts
    )
    return construct_event_message(msg_id, b"".join((b"{", payload, b"}")))
