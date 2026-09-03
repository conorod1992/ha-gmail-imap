"""Regression tests for watch UID acknowledgement and retry behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.coordinator import (
    EmailDataUpdateCoordinator,
    watch_definition_fingerprint,
)
from custom_components.email_ha.imap_client import ImapClientError


def _coordinator() -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = "user@example.com"  # noqa: SLF001
    coordinator._folder = "INBOX"  # noqa: SLF001
    coordinator._folder_uid_state = {}  # noqa: SLF001
    coordinator._restored_folders = set()  # noqa: SLF001
    coordinator._watch_uid_state = {}  # noqa: SLF001
    coordinator._custom_last_new_match = {}  # noqa: SLF001
    coordinator._watch_last_new_match = {}  # noqa: SLF001
    coordinator._watch_listeners = {}  # noqa: SLF001
    coordinator._rule_health = {}  # noqa: SLF001
    coordinator.email_watches = []
    coordinator.custom_sensors = []
    return coordinator


def _message(uid: str) -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "subject": f"Message {uid}",
        "sender": {"name": "Sender", "address": "sender@example.com"},
        "date": "2026-09-03T01:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_watch_match_failure_retries_unacknowledged_arrival() -> None:
    """A transient watch query failure retries the same UID on the next refresh."""
    coordinator = _coordinator()
    watch = {
        "id": "watch",
        "name": "Watch",
        "folder": "Receipts",
        "enabled": True,
        "filters": {"from": "example.com"},
    }
    coordinator.email_watches = [watch]
    fingerprint = watch_definition_fingerprint(watch)
    coordinator._folder_uid_state["Receipts"] = (9, 100)  # noqa: SLF001
    coordinator._watch_uid_state["watch"] = (fingerprint, 9, 100)  # noqa: SLF001

    client = AsyncMock()
    client.get_new_emails.return_value = ([_message("101")], 1)
    status = {"uidvalidity": 9, "uidnext": 102}

    first_arrivals = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        status,
        fetch_messages=True,
    )
    first_floors = coordinator._prepare_watch_uid_floors(  # noqa: SLF001
        {"Receipts": status}
    )

    assert first_floors == {"watch": 100}
    assert coordinator._watch_uid_state["watch"] == (fingerprint, 9, 100)  # noqa: SLF001

    client.matching_uids.side_effect = ImapClientError("temporary failure")
    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client,
        {"Receipts": first_arrivals},
        watch_uid_floors=first_floors,
    )

    assert matches == []
    assert coordinator._folder_uid_state["Receipts"] == (9, 100)  # noqa: SLF001
    assert coordinator._watch_uid_state["watch"] == (fingerprint, 9, 100)  # noqa: SLF001

    client.matching_uids.side_effect = None
    client.matching_uids.return_value = {"101"}
    retry_arrivals = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        status,
        fetch_messages=True,
    )
    retry_floors = coordinator._prepare_watch_uid_floors(  # noqa: SLF001
        {"Receipts": status}
    )
    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client,
        {"Receipts": retry_arrivals},
        watch_uid_floors=retry_floors,
    )

    assert [(watch_id, message["uid"]) for watch_id, message in matches] == [
        ("watch", "101")
    ]
    assert coordinator._folder_uid_state["Receipts"] == (9, 101)  # noqa: SLF001
    assert coordinator._watch_uid_state["watch"] == (fingerprint, 9, 101)  # noqa: SLF001
    assert client.get_new_emails.await_count == 2
