# pyright: reportArgumentType=false
"""Tests for the canonical New email EventEntity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.email_ha.const import (
    EVENT_TYPE_NEW_EMAIL,
    EVENT_TYPE_NEW_MATCHING_EMAIL,
    PLATFORMS,
)
from custom_components.email_ha.coordinator import RuleHealthData
from custom_components.email_ha.event import EmailWatchEventEntity, NewEmailEventEntity


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


def test_watch_rename_preserves_identity_and_emits_bounded_payload() -> None:
    """A watch UUID, not its mutable display name, determines entity identity."""
    entry = SimpleNamespace(
        entry_id="entry-1", data={"email": "user@example.com"}, options={}
    )
    coordinator = SimpleNamespace(
        async_add_watch_listener=Mock(),
        rule_health=Mock(return_value=RuleHealthData()),
    )
    entity = EmailWatchEventEntity(
        coordinator,
        entry,
        {"id": "watch-uuid", "name": "Renamed RSA", "folder": "INBOX"},
    )
    entity._trigger_event = Mock()  # type: ignore[method-assign]  # noqa: SLF001
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign]
    payload = {
        "watch_id": "watch-uuid",
        "watch_name": "Renamed RSA",
        "uid": "44",
        "subject": "Check test",
    }

    entity._handle_match(payload)  # noqa: SLF001

    assert entity.unique_id == "entry-1_watch_watch-uuid"
    assert entity.name == "Renamed RSA"
    entity._trigger_event.assert_called_once_with(  # type: ignore[attr-defined]  # noqa: SLF001
        EVENT_TYPE_NEW_MATCHING_EMAIL, payload
    )
    assert "body" not in payload


def test_watch_exposes_pause_and_query_health_without_filter_values() -> None:
    """Watch attributes expose actionable health without private filter content."""
    entry = SimpleNamespace(
        entry_id="entry-1", data={"email": "user@example.com"}, options={}
    )
    health = RuleHealthData(
        status="Error",
        last_successful_check="2026-07-28T09:00:00+00:00",
        last_error_at="2026-07-28T10:00:00+00:00",
        last_error_type="FolderQueryError",
        last_error="Configured folder could not be queried",
    )
    coordinator = SimpleNamespace(
        rule_health=Mock(return_value=health),
        _watch_last_new_match={"watch-uuid": "2026-07-28T09:30:00+00:00"},
    )
    entity = EmailWatchEventEntity(
        coordinator,
        entry,
        {
            "id": "watch-uuid",
            "name": "RSA",
            "folder": "Receipts",
            "enabled": True,
            "catch_up": True,
            "filters": {"from": "private.example"},
        },
    )

    assert entity.extra_state_attributes == {
        "folder": "Receipts",
        "enabled": True,
        "catch_up": True,
        "last_new_match": "2026-07-28T09:30:00+00:00",
        "rule_status": "Error",
        "last_successful_check": "2026-07-28T09:00:00+00:00",
        "last_error_at": "2026-07-28T10:00:00+00:00",
        "last_error_type": "FolderQueryError",
        "last_error": "Configured folder could not be queried",
    }
    assert "private.example" not in str(entity.extra_state_attributes)


def test_watch_without_prior_match_exposes_null_timestamp() -> None:
    """A watch with no persisted match history reports no last match."""
    entry = SimpleNamespace(
        entry_id="entry-1", data={"email": "user@example.com"}, options={}
    )
    coordinator = SimpleNamespace(
        rule_health=Mock(return_value=RuleHealthData()),
        _watch_last_new_match={},
    )
    entity = EmailWatchEventEntity(
        coordinator,
        entry,
        {"id": "new-watch", "name": "New", "folder": "INBOX"},
    )

    assert entity.extra_state_attributes["last_new_match"] is None
