"""Tests for bounded restart catch-up on Email watches."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.email_ha.const import MAX_CATCH_UP_EVENTS
from custom_components.email_ha.coordinator import (
    EmailDataUpdateCoordinator,
    watch_definition_fingerprint,
)


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


@pytest.mark.asyncio
async def test_new_watch_does_not_inherit_existing_watch_catch_up_history() -> None:
    """A new watch sharing a folder starts at current UID while an old watch catches up."""
    coordinator = _coordinator()
    established = {
        "id": "established",
        "name": "Established",
        "folder": "Receipts",
        "enabled": True,
        "catch_up": True,
        "filters": {"from": "example.com"},
    }
    new_watch = {
        "id": "new",
        "name": "New",
        "folder": "Receipts",
        "enabled": True,
        "catch_up": True,
        "filters": {"from": "example.com"},
    }
    coordinator.email_watches = [established, new_watch]
    coordinator._watch_uid_state["established"] = (  # noqa: SLF001
        watch_definition_fingerprint(established),
        9,
        100,
    )

    floors = coordinator._prepare_watch_uid_floors(  # noqa: SLF001
        {"Receipts": {"uidvalidity": 9, "uidnext": 104}}
    )

    assert floors == {"established": 100, "new": 103}
    client = AsyncMock()
    client.matching_uids.return_value = {"101", "102", "103"}
    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client,
        {"Receipts": [_message("101"), _message("102"), _message("103")]},
        {"Receipts"},
        floors,
    )

    assert [(watch_id, message["uid"]) for watch_id, message in matches] == [
        ("established", "101"),
        ("established", "102"),
        ("established", "103"),
    ]
    client.matching_uids.assert_awaited_once_with(
        "Receipts",
        ["101", "102", "103"],
        ["FROM", '"example.com"'],
    )


def test_material_watch_edit_rebaselines_but_name_edit_keeps_floor() -> None:
    """Only fields that change matching/catch-up semantics reset eligibility."""
    coordinator = _coordinator()
    watch = {
        "id": "watch",
        "name": "Original name",
        "folder": "Receipts",
        "catch_up": True,
        "filters": {"from": "example.com"},
    }
    coordinator.email_watches = [watch]
    coordinator._watch_uid_state["watch"] = (  # noqa: SLF001
        watch_definition_fingerprint(watch),
        9,
        100,
    )

    watch["name"] = "Renamed only"
    unchanged = coordinator._prepare_watch_uid_floors(  # noqa: SLF001
        {"Receipts": {"uidvalidity": 9, "uidnext": 104}}
    )
    assert unchanged["watch"] == 100

    watch["filters"] = {"subject": "receipt"}
    changed = coordinator._prepare_watch_uid_floors(  # noqa: SLF001
        {"Receipts": {"uidvalidity": 9, "uidnext": 105}}
    )
    assert changed["watch"] == 104


@pytest.mark.asyncio
async def test_legacy_state_migrates_existing_watch_from_folder_baseline() -> None:
    """The first upgrade keeps established catch-up eligibility from old state."""
    coordinator = _coordinator()
    watch = {
        "id": "watch",
        "folder": "Receipts",
        "catch_up": True,
        "filters": {"from": "example.com"},
    }
    coordinator.email_watches = [watch]
    state_store = SimpleNamespace(
        async_load=AsyncMock(),
        folder_uid_state={"Receipts": (9, 100)},
        watch_uid_state={},
        has_watch_uid_state=False,
        custom_last_new_match={},
        watch_last_new_match={},
        async_schedule_save=Mock(),
    )
    cast(Any, coordinator)._state_store = state_store  # noqa: SLF001

    await coordinator.async_load_state()

    assert coordinator._watch_uid_state["watch"] == (  # noqa: SLF001
        watch_definition_fingerprint(watch),
        9,
        100,
    )
    state_store.async_schedule_save.assert_called_once()


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
