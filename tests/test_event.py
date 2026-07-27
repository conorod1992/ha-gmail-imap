# pyright: reportArgumentType=false
"""Tests for the discoverable new-email event entity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.email_ha.const import EVENT_TYPE_NEW_EMAIL, PLATFORMS
from custom_components.email_ha.event import NewEmailEventEntity


def _entity() -> NewEmailEventEntity:
    entry = SimpleNamespace(entry_id="entry-1", data={"email": "user@example.com"})
    entity = NewEmailEventEntity(entry)
    entity._trigger_event = Mock()  # type: ignore[method-assign]  # noqa: SLF001
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign]
    return entity


def test_event_platform_is_forwarded() -> None:
    """The integration loads the event platform alongside sensors."""
    assert "event" in PLATFORMS


def test_event_entity_forwards_matching_account_event() -> None:
    """A matching legacy event becomes a discoverable entity event."""
    entity = _entity()
    event_data = {
        "config_entry_id": "entry-1",
        "subject": "Example",
        "sender_email": "sender@example.com",
    }

    entity._handle_new_email(SimpleNamespace(data=event_data))  # noqa: SLF001

    entity._trigger_event.assert_called_once_with(  # type: ignore[attr-defined]  # noqa: SLF001
        EVENT_TYPE_NEW_EMAIL, event_data
    )
    entity.async_write_ha_state.assert_called_once()  # type: ignore[attr-defined]


def test_event_entity_ignores_other_account_event() -> None:
    """Each account entity listens only to its own config entry."""
    entity = _entity()

    entity._handle_new_email(  # noqa: SLF001
        SimpleNamespace(data={"config_entry_id": "entry-2"})
    )

    entity._trigger_event.assert_not_called()  # type: ignore[attr-defined]  # noqa: SLF001
    entity.async_write_ha_state.assert_not_called()  # type: ignore[attr-defined]
