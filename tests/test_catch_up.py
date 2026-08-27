"""Tests for bounded restart catch-up on Email watches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.const import MAX_CATCH_UP_EVENTS
from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator


def _coordinator() -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = "user@example.com"  # noqa: SLF001
    coordinator._folder_uid_state = {}  # noqa: SLF001
    coordinator._restored_folders = set()  # noqa: SLF001
    coordinator._custom_last_new_match = {}  # noqa: SLF001
    coordinator._watch_last_new_match = {}  # noqa: SLF001
    coordinator._watch_listeners = {}  # noqa: SLF001
    coordinator.email_watches = []
    coordinator.custom_sensors = []
    return coordinator


def _message(uid: str) -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "subject": f"Message {uid}",
        "sender": {"name": "Sender", "address": "sender@example.com"},
        "date": "2026-08-27T12:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_restored_baseline_without_opt_in_does_not_replay() -> None:
    """A restored live-only watch advances its baseline without replaying mail."""
    coordinator = _coordinator()
    coordinator._folder_uid_state["Receipts"] = (9, 100)  # noqa: SLF001
    coordinator._restored_folders.add("Receipts")  # noqa: SLF001
    client = AsyncMock()

    messages = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 104},
        fetch_messages=True,
        allow_catch_up=False,
    )

    assert messages == []
    assert coordinator._folder_uid_state["Receipts"] == (9, 103)  # noqa: SLF001
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_opted_in_restored_baseline_fetches_bounded_missed_mail() -> None:
    """An opted-in restored watch fetches missed mail with the catch-up limit."""
    coordinator = _coordinator()
    coordinator._folder_uid_state["Receipts"] = (9, 100)  # noqa: SLF001
    coordinator._restored_folders.add("Receipts")  # noqa: SLF001
    client = AsyncMock()
    client.get_new_emails.return_value = ([_message("101"), _message("102")], 2)

    messages = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 103},
        fetch_messages=True,
        allow_catch_up=True,
    )

    assert [message["uid"] for message in messages] == ["101", "102"]
    client.get_new_emails.assert_awaited_once_with("Receipts", 100, MAX_CATCH_UP_EVENTS)


@pytest.mark.asyncio
async def test_uidvalidity_change_never_catches_up_uncertain_history() -> None:
    """A UIDVALIDITY change re-baselines instead of replaying uncertain history."""
    coordinator = _coordinator()
    coordinator._folder_uid_state["Receipts"] = (9, 100)  # noqa: SLF001
    coordinator._restored_folders.add("Receipts")  # noqa: SLF001
    client = AsyncMock()

    messages = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 10, "uidnext": 8},
        fetch_messages=True,
        allow_catch_up=True,
    )

    assert messages == []
    assert coordinator._folder_uid_state["Receipts"] == (10, 7)  # noqa: SLF001
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_batch_matches_only_opted_in_watch() -> None:
    """A restart catch-up batch matches only watches that opted into catch-up."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {
            "id": "catch-up",
            "name": "Catch up",
            "folder": "Receipts",
            "enabled": True,
            "catch_up": True,
            "filters": {"from": "example.com"},
        },
        {
            "id": "live-only",
            "name": "Live only",
            "folder": "Receipts",
            "enabled": True,
            "filters": {"from": "example.com"},
        },
    ]
    coordinator.custom_sensors = [
        {"id": "count", "folder": "Receipts", "filters": {"from": "example.com"}}
    ]
    client = AsyncMock()
    client.matching_uids.return_value = {"101"}

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client,
        {"Receipts": [_message("101")]},
        {"Receipts"},
    )

    assert [(watch_id, message["uid"]) for watch_id, message in matches] == [
        ("catch-up", "101")
    ]
    assert coordinator._custom_last_new_match == {}  # noqa: SLF001
    client.matching_uids.assert_awaited_once()


def test_caught_up_event_is_marked_and_remains_body_free() -> None:
    """Catch-up events are marked while retaining the body-free event contract."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {"id": "watch", "name": "Watch", "folder": "INBOX", "enabled": True}
    ]
    received: list[dict] = []
    coordinator.async_add_watch_listener("watch", received.append)
    caught_up = _message("101")
    caught_up["_email_ha_caught_up"] = True

    coordinator._notify_watch_matches([("watch", caught_up)])  # noqa: SLF001
    coordinator._notify_watch_matches([("watch", _message("102"))])  # noqa: SLF001

    assert [event["caught_up"] for event in received] == [True, False]
    assert "_email_ha_caught_up" not in received[0]
    assert "body" not in received[0]


@pytest.mark.asyncio
async def test_disabled_catch_up_watch_still_does_no_matching_work() -> None:
    """A disabled catch-up watch performs no matching queries."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {
            "id": "paused",
            "folder": "Receipts",
            "enabled": False,
            "catch_up": True,
            "filters": {"from": "example.com"},
        }
    ]
    client = AsyncMock()

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client, {"Receipts": [_message("101")]}, {"Receipts"}
    )

    assert matches == []
    client.matching_uids.assert_not_awaited()
