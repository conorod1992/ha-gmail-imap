# pyright: reportAttributeAccessIssue=false
"""Tests for UIDVALIDITY-aware, bounded new-email detection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator


def _coordinator(account: str = "user@example.com") -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = account  # noqa: SLF001
    coordinator._folder = "INBOX"  # noqa: SLF001
    coordinator._event_baseline_ready = False  # noqa: SLF001
    coordinator._uid_validity = None  # noqa: SLF001
    coordinator._last_seen_uid = 0  # noqa: SLF001
    coordinator.enabled_gmail_entities = {"new_email"}
    coordinator._new_email_listeners = set()  # noqa: SLF001
    return coordinator


def _message(uid: str) -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "subject": f"Message {uid}",
        "sender": {"name": "Sender", "address": "sender@example.com"},
        "date": "2026-07-28T10:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_initial_mailbox_baseline_emits_nothing() -> None:
    """Existing mail establishes UIDVALIDITY/UIDNEXT without a search."""
    coordinator = _coordinator()
    client = AsyncMock()

    result = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 11}
    )

    assert result == []
    assert coordinator._last_seen_uid == 10  # noqa: SLF001
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_new_message_is_detected_once() -> None:
    """A UID above the baseline is returned and advances the baseline."""
    coordinator = _coordinator()
    client = AsyncMock()
    await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 11}
    )
    client.get_new_emails.return_value = ([_message("11")], 1)

    first = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 12}
    )
    second = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 12}
    )

    assert [message["uid"] for message in first] == ["11"]
    assert second == []
    client.get_new_emails.assert_awaited_once_with("INBOX", 10, 25)


@pytest.mark.asyncio
async def test_deleting_newest_does_not_report_older_mail() -> None:
    """Unchanged UIDNEXT stays quiet even when mailbox contents shrink."""
    coordinator = _coordinator()
    client = AsyncMock()
    await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 50}
    )

    assert (
        await coordinator._async_detect_new_emails(  # noqa: SLF001
            client, {"uidvalidity": 7, "uidnext": 50, "messages": 2}
        )
        == []
    )
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_event_advances_without_fetching_headers() -> None:
    """An unselected New email entity does not cause unsolicited header fetches."""
    coordinator = _coordinator()
    coordinator.enabled_gmail_entities = set()
    client = AsyncMock()
    await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 10}
    )

    result = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 12}
    )

    assert result == []
    assert coordinator._last_seen_uid == 11  # noqa: SLF001
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_does_not_replay_and_uidvalidity_reset_rebaselines() -> None:
    """The coordinator retains its baseline and safely resets on UID generation change."""
    coordinator = _coordinator()
    client = AsyncMock()
    await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 50}
    )

    reconnect = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 7, "uidnext": 50}
    )
    reset = await coordinator._async_detect_new_emails(  # noqa: SLF001
        client, {"uidvalidity": 8, "uidnext": 4}
    )

    assert reconnect == []
    assert reset == []
    assert coordinator._last_seen_uid == 3  # noqa: SLF001


def test_multiple_events_are_delivered_oldest_to_newest_without_bodies() -> None:
    """Each arrival becomes one privacy-conscious EventEntity payload."""
    coordinator = _coordinator()
    received: list[dict] = []
    coordinator.async_add_new_email_listener(received.append)

    coordinator._notify_new_emails([_message("11"), _message("12")])  # noqa: SLF001

    assert [event["uid"] for event in received] == ["11", "12"]
    assert received[0]["account"] == "user@example.com"
    assert received[0]["sender_address"] == "sender@example.com"
    assert "plain_text_body" not in received[0]


def test_account_listeners_are_isolated() -> None:
    """Separate coordinators cannot deliver events to another account entity."""
    first = _coordinator("first@example.com")
    second = _coordinator("second@example.com")
    first_events: list[dict] = []
    second_events: list[dict] = []
    first.async_add_new_email_listener(first_events.append)
    second.async_add_new_email_listener(second_events.append)

    first._notify_new_emails([_message("1")])  # noqa: SLF001

    assert len(first_events) == 1
    assert second_events == []
