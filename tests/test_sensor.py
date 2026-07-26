# pyright: reportArgumentType=false
"""Tests for backwards-compatible and optional sensor semantics."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.email_ha.coordinator import EmailData, SearchCountData
from custom_components.email_ha.sensor import SearchCountSensor, UnreadCountSensor


def _entry():
    return SimpleNamespace(
        entry_id="entry-1",
        data={"email": "user@example.com", "folder": "INBOX"},
        options={},
    )


def test_existing_unread_sensor_semantics_and_unique_id_are_unchanged() -> None:
    """Unread remains the configured-folder UNSEEN status and keeps its suffix."""
    coordinator = SimpleNamespace(
        data=EmailData(unread_count=340),
        last_success_time=datetime.now(timezone.utc),
        last_update_success=True,
    )
    sensor = UnreadCountSensor(coordinator, _entry())

    assert sensor.unique_id == "entry-1_unread_count"
    assert sensor.native_value == 340
    assert sensor.extra_state_attributes == {"folder": "INBOX"}


def test_search_sensor_count_and_unique_id() -> None:
    """An optional sensor exposes its server-side count and stable monitor ID."""
    coordinator = SimpleNamespace(
        data=EmailData(search_counts={"monitor-1": SearchCountData(2, "99")}),
        last_success_time=datetime.now(timezone.utc),
        last_update_success=True,
    )
    sensor = SearchCountSensor(
        coordinator,
        _entry(),
        {
            "id": "monitor-1",
            "name": "RSA unread",
            "folder": "INBOX",
            "filters": {"from": "rsa.ie", "read_state": "unread"},
        },
    )

    assert sensor.unique_id == "entry-1_search_monitor-1"
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes == {
        "folder": "INBOX",
        "filter_types": ["from", "read_state"],
        "newest_matching_uid": "99",
    }
