# pyright: reportArgumentType=false
"""Tests for fixed Gmail concepts and privacy-conscious custom sensors."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.email_ha.coordinator import EmailData, SearchCountData
from custom_components.email_ha.gmail import GMAIL_ENTITIES
from custom_components.email_ha.sensor import CustomEmailCountSensor, GmailSensor


def _entry(enabled: list[str] | None = None):
    return SimpleNamespace(
        entry_id="entry-1",
        data={"email": "user@example.com"},
        options={"gmail_entities": enabled or []},
    )


def _coordinator(data: EmailData):
    return SimpleNamespace(
        data=data,
        folder="INBOX",
        last_success_time=datetime.now(timezone.utc),
        last_update_success=True,
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("primary_unread", 3),
        ("important_unread", 2),
        ("starred_unread", 1),
        ("updates_unread", 4),
        ("promotions_unread", 5),
        ("social_unread", 6),
        ("forums_unread", 7),
    ],
)
def test_fixed_gmail_search_sensor_values(key: str, expected: int) -> None:
    """Every Gmail classification is a proper fixed sensor."""
    counts = {
        "primary_unread": SearchCountData(3, "13"),
        "important_unread": SearchCountData(2, "12"),
        "starred_unread": SearchCountData(1, "11"),
        "updates_unread": SearchCountData(4, "14"),
        "promotions_unread": SearchCountData(5, "15"),
        "social_unread": SearchCountData(6, "16"),
        "forums_unread": SearchCountData(7, "17"),
    }
    sensor = GmailSensor(
        _coordinator(EmailData(gmail_counts=counts)),
        _entry([key]),
        GMAIL_ENTITIES[key],
    )

    assert sensor.native_value == expected
    assert sensor.unique_id == f"entry-1_{key}"
    assert sensor.entity_registry_enabled_default is True
    assert sensor.extra_state_attributes == {"folder": "INBOX"}


def test_inbox_counts_and_optional_defaults() -> None:
    """Inbox unread differs from total Inbox messages and both are optional."""
    data = EmailData(inbox_unread=8, inbox_total=42)
    unread = GmailSensor(_coordinator(data), _entry([]), GMAIL_ENTITIES["inbox_unread"])
    total = GmailSensor(
        _coordinator(data), _entry([]), GMAIL_ENTITIES["inbox_messages"]
    )

    assert unread.native_value == 8
    assert total.native_value == 42
    assert unread.entity_registry_enabled_default is False
    assert total.entity_registry_enabled_default is False


def test_latest_email_metadata_uses_canonical_sender_shape() -> None:
    """Latest email exposes bounded header metadata and no body."""
    data = EmailData(
        emails=[
            {
                "uid": "9",
                "subject": "Booking",
                "sender": {"name": "Hotel", "address": "hotel@example.com"},
                "date": "2026-07-28T09:00:00+00:00",
            }
        ]
    )
    sensor = GmailSensor(
        _coordinator(data),
        _entry(["latest_email"]),
        GMAIL_ENTITIES["latest_email"],
    )

    assert sensor.native_value == "Booking"
    assert sensor.extra_state_attributes == {
        "sender_name": "Hotel",
        "sender_address": "hotel@example.com",
        "date": "2026-07-28T09:00:00+00:00",
        "uid": "9",
        "folder": "INBOX",
    }


def test_mailbox_folders_is_disabled_by_default() -> None:
    """Folder count/list remains discoverable without normal entity clutter."""
    sensor = GmailSensor(
        _coordinator(EmailData(folders=["INBOX", "[Gmail]/All Mail"])),
        _entry([]),
        GMAIL_ENTITIES["mailbox_folders"],
    )

    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["folders"] == [
        "INBOX",
        "[Gmail]/All Mail",
    ]
    assert sensor.entity_registry_enabled_default is False


def test_custom_sensor_hides_private_filter_values() -> None:
    """Entity attributes identify filter types but not owner-entered values."""
    sensor = CustomEmailCountSensor(
        _coordinator(
            EmailData(
                custom_counts={
                    "custom-1": SearchCountData(
                        2,
                        "99",
                        "RSA renewal",
                        "RSA",
                        "sender@example.com",
                        "2026-07-28T10:00:00+00:00",
                        "2026-07-28T10:01:00+00:00",
                    )
                }
            )
        ),
        _entry(),
        {
            "id": "custom-1",
            "name": "RSA unread",
            "folder": "INBOX",
            "filters": {"from": "rsa.ie", "read_state": "unread"},
        },
    )

    assert sensor.native_value == 2
    assert sensor.unique_id == "entry-1_custom_custom-1"
    assert sensor.extra_state_attributes == {
        "folder": "INBOX",
        "filter_types": ["from", "read_state"],
        "newest_matching_uid": "99",
        "newest_matching_subject": "RSA renewal",
        "newest_matching_sender_name": "RSA",
        "newest_matching_sender_address": "sender@example.com",
        "newest_matching_date": "2026-07-28T10:00:00+00:00",
        "last_new_match": "2026-07-28T10:01:00+00:00",
    }
    assert "rsa.ie" not in str(sensor.extra_state_attributes)
