"""Tests for secondary-folder Email Watch refresh cadence."""

from datetime import timedelta

from custom_components.email_ha.coordinator import _coordinator_refresh_interval


def test_default_refresh_interval_without_secondary_watch() -> None:
    """Normal monitored-folder setups keep the existing 15-minute fallback."""
    watches = [
        {"id": "inbox", "folder": "INBOX", "enabled": True},
        {"id": "paused", "folder": "Receipts", "enabled": False},
    ]

    assert _coordinator_refresh_interval("INBOX", watches) == timedelta(minutes=15)


def test_secondary_watch_uses_one_minute_fallback() -> None:
    """An enabled watch outside the IDLE folder gets a near-real-time poll fallback."""
    watches = [
        {"id": "inbox", "folder": "INBOX", "enabled": True},
        {"id": "receipts", "folder": "Receipts", "enabled": True},
    ]

    assert _coordinator_refresh_interval("INBOX", watches) == timedelta(minutes=1)


def test_default_folder_is_secondary_when_monitoring_another_folder() -> None:
    """A watch using the default INBOX is secondary if another folder is monitored."""
    watches = [{"id": "inbox", "enabled": True}]

    assert _coordinator_refresh_interval("Receipts", watches) == timedelta(minutes=1)
