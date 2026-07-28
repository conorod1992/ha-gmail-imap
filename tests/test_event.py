# pyright: reportArgumentType=false
"""Tests for the canonical New email EventEntity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.email_ha.const import EVENT_TYPE_NEW_EMAIL, PLATFORMS
from custom_components.email_ha.event import NewEmailEventEntity


def _entity() -> NewEmailEventEntity:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"email": "user@example.com"},
        options={"gmail_entities": ["new_email"]},
    )
    coordinator = SimpleNamespace(async_add_new_email_listener=Mock())
    entity = NewEmailEventEntity(coordinator, entry)
    entity._trigger_event = Mock()  # type: ignore[method-assign]  # noqa: SLF001
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign]
    return entity


def test_event_platform_and_default_are_enabled() -> None:
    """The discoverable event platform is loaded and recommended."""
    entity = _entity()

    assert "event" in PLATFORMS
    assert entity.entity_registry_enabled_default is True
    assert entity.unique_id == "entry-1_new_email"


def test_event_entity_publishes_coordinator_payload() -> None:
    """No public raw bus event is needed between coordinator and entity."""
    entity = _entity()
    event_data = {
        "account": "user@example.com",
        "uid": "11",
        "subject": "Example",
        "sender_address": "sender@example.com",
    }

    entity._handle_new_email(event_data)  # noqa: SLF001

    entity._trigger_event.assert_called_once_with(  # type: ignore[attr-defined]  # noqa: SLF001
        EVENT_TYPE_NEW_EMAIL, event_data
    )
    entity.async_write_ha_state.assert_called_once()  # type: ignore[attr-defined]
