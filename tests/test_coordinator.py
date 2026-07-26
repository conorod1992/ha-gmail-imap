# pyright: reportAttributeAccessIssue=false
"""Tests for bounded, duplicate-resistant new-mail events."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.email_ha.const import EVENT_NEW_EMAIL
from custom_components.email_ha.coordinator import EmailData, EmailDataUpdateCoordinator


def _coordinator_for_events() -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
    coordinator._email = "user@example.com"  # noqa: SLF001
    coordinator._folder = "INBOX"  # noqa: SLF001
    coordinator._last_uid = None  # noqa: SLF001
    coordinator.config_entry = SimpleNamespace(entry_id="entry-1")
    return coordinator


def _data(uid: str) -> EmailData:
    return EmailData(
        emails=[
            {
                "uid": uid,
                "subject": "Example",
                "sender_name": "Sender",
                "sender_email": "sender@example.com",
                "preview": "",
            }
        ]
    )


def test_initial_refresh_does_not_flood_new_email_events() -> None:
    """Existing mail establishes the baseline without firing an event."""
    coordinator = _coordinator_for_events()

    coordinator._fire_new_email_event(_data("10"))  # noqa: SLF001

    coordinator.hass.bus.async_fire.assert_not_called()


def test_refreshes_do_not_duplicate_new_email_events() -> None:
    """A newly observed UID fires once and ordinary refreshes remain quiet."""
    coordinator = _coordinator_for_events()
    coordinator._fire_new_email_event(_data("10"))  # noqa: SLF001
    coordinator._fire_new_email_event(_data("11"))  # noqa: SLF001
    coordinator._fire_new_email_event(_data("11"))  # noqa: SLF001

    coordinator.hass.bus.async_fire.assert_called_once()
    event_type, event_data = coordinator.hass.bus.async_fire.call_args.args
    assert event_type == EVENT_NEW_EMAIL
    assert event_data["uid"] == "11"
    assert "plain_text_body" not in event_data
