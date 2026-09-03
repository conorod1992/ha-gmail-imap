# pyright: reportAttributeAccessIssue=false
"""Tests for UIDVALIDITY-aware, bounded new-email detection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.coordinator import (
    EmailDataUpdateCoordinator,
    watch_definition_fingerprint,
)
from custom_components.email_ha.imap_client import ImapClientError, ImapFolderError


def _coordinator(account: str = "user@example.com") -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = account  # noqa: SLF001
    coordinator._folder = "INBOX"  # noqa: SLF001
    coordinator._event_baseline_ready = False  # noqa: SLF001
    coordinator._uid_validity = None  # noqa: SLF001
    coordinator._last_seen_uid = 0  # noqa: SLF001
    coordinator.enabled_gmail_entities = {"new_email"}
    coordinator._new_email_listeners = set()  # noqa: SLF001
    coordinator.email_watches = []
    coordinator.custom_sensors = []
    coordinator._watch_listeners = {}  # noqa: SLF001
    coordinator._folder_uid_state = {}  # noqa: SLF001
    coordinator._watch_uid_state = {}  # noqa: SLF001
    coordinator._custom_last_new_match = {}  # noqa: SLF001
    coordinator._cached_folders = ["INBOX"]  # noqa: SLF001
    coordinator._folders_fetched_at = float("inf")  # noqa: SLF001
    coordinator.last_success_time = None
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


@pytest.mark.asyncio
async def test_watched_folder_baseline_never_replays_historical_mail() -> None:
    """Startup and UIDVALIDITY changes establish a baseline without fetching."""
    coordinator = _coordinator()
    client = AsyncMock()

    startup = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 101},
        fetch_messages=True,
    )
    reload_same_mail = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 101},
        fetch_messages=True,
    )

    assert startup == reload_same_mail == []
    client.get_new_emails.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_email_can_match_multiple_watches_and_custom_sensor() -> None:
    """One bounded new UID may feed multiple watches and last-new sensor state."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {
            "id": "rsa",
            "name": "RSA emails",
            "folder": "INBOX",
            "filters": {"from": "rsa.ie"},
        },
        {
            "id": "pdf",
            "name": "PDF emails",
            "folder": "INBOX",
            "filters": {"attachment_filename": "pdf"},
        },
    ]
    coordinator.custom_sensors = [
        {
            "id": "rsa-count",
            "name": "RSA count",
            "folder": "INBOX",
            "filters": {"from": "rsa.ie"},
        }
    ]
    client = AsyncMock()
    client.matching_uids.return_value = {"44", "45"}

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client, {"INBOX": [_message("44"), _message("45")]}
    )

    assert [(watch_id, message["uid"]) for watch_id, message in matches] == [
        ("rsa", "44"),
        ("rsa", "45"),
        ("pdf", "44"),
        ("pdf", "45"),
    ]
    assert coordinator._custom_last_new_match["rsa-count"]  # noqa: SLF001
    assert client.matching_uids.await_count == 3
    assert all(
        call.args[1] == ["44", "45"] for call in client.matching_uids.await_args_list
    )


@pytest.mark.asyncio
async def test_nonmatching_arrival_updates_neither_watch_nor_sensor() -> None:
    """A new UID alone is insufficient; the structured filters must match it."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {"id": "rsa", "folder": "INBOX", "filters": {"from": "rsa.ie"}}
    ]
    coordinator.custom_sensors = [
        {"id": "rsa-count", "folder": "INBOX", "filters": {"from": "rsa.ie"}}
    ]
    client = AsyncMock()
    client.matching_uids.return_value = set()

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client, {"INBOX": [_message("44")]}
    )

    assert matches == []
    assert coordinator._custom_last_new_match == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_disabled_watch_skips_matching_and_emission() -> None:
    """A legacy-compatible disabled watch performs no filter search or event."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {
            "id": "paused",
            "name": "Paused",
            "folder": "INBOX",
            "enabled": False,
            "filters": {"from": "private.example"},
        }
    ]
    received: list[dict] = []
    coordinator.async_add_watch_listener("paused", received.append)
    client = AsyncMock()

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client, {"INBOX": [_message("44")]}
    )
    coordinator._notify_watch_matches([("paused", _message("44"))])  # noqa: SLF001

    assert matches == []
    assert received == []
    client.matching_uids.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_folder_baseline_prevents_replay_after_enable() -> None:
    """Paused mail advances the UID baseline; only later mail fires after resume."""
    coordinator = _coordinator()
    client = AsyncMock()
    await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 101},
        fetch_messages=False,
    )
    await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 104},
        fetch_messages=False,
    )

    replay = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 104},
        fetch_messages=True,
    )
    client.get_new_emails.return_value = ([_message("104")], 1)
    future = await coordinator._async_detect_folder_new_emails(  # noqa: SLF001
        client,
        "Receipts",
        {"uidvalidity": 9, "uidnext": 105},
        fetch_messages=True,
    )

    assert replay == []
    assert [message["uid"] for message in future] == ["104"]
    client.get_new_emails.assert_awaited_once_with("Receipts", 103, 25)


@pytest.mark.asyncio
async def test_matching_results_map_only_to_arrival_metadata() -> None:
    """A server response cannot introduce a historical UID outside the arrival set."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {"id": "rsa", "folder": "INBOX", "filters": {"from": "rsa.ie"}}
    ]
    client = AsyncMock()
    client.matching_uids.return_value = {"45", "999"}

    matches = await coordinator._async_match_new_messages(  # noqa: SLF001
        client, {"INBOX": [_message("44"), _message("45")]}
    )

    assert [(watch_id, message["uid"]) for watch_id, message in matches] == [
        ("rsa", "45")
    ]
    client.matching_uids.assert_awaited_once_with(
        "INBOX", ["44", "45"], ["FROM", '"rsa.ie"']
    )


@pytest.mark.asyncio
async def test_inaccessible_secondary_watch_folder_is_isolated(caplog) -> None:
    """A missing watch folder does not fail healthy account state or emit a match."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {
            "id": "missing",
            "name": "Missing",
            "folder": "Deleted",
            "filters": {"from": "private@example.com"},
        }
    ]
    coordinator._event_baseline_ready = True  # noqa: SLF001
    coordinator._uid_validity = 7  # noqa: SLF001
    coordinator._last_seen_uid = 9  # noqa: SLF001
    client = AsyncMock()

    async def folder_status(folder: str) -> dict[str, int]:
        if folder == "Deleted":
            raise ImapFolderError("not accessible")
        return {"messages": 4, "unseen": 2, "uidvalidity": 7, "uidnext": 11}

    client.get_folder_status.side_effect = folder_status
    client.get_new_emails.return_value = ([_message("10")], 1)

    with caplog.at_level("WARNING"):
        data = await coordinator._async_fetch_data(client)  # noqa: SLF001

    assert data.inbox_total == 4
    assert [message["uid"] for message in data.new_emails] == ["10"]
    assert data.watch_matches == []
    client.matching_uids.assert_not_awaited()
    assert "Deleted" in caplog.text
    assert "user@example.com" in caplog.text
    assert "private@example.com" not in caplog.text


@pytest.mark.asyncio
async def test_inaccessible_custom_folder_does_not_block_healthy_sensor() -> None:
    """Per-sensor failure remains isolated while another folder still updates."""
    coordinator = _coordinator()
    coordinator.custom_sensors = [
        {"id": "missing", "folder": "Deleted", "filters": {}},
        {"id": "healthy", "folder": "Receipts", "filters": {}},
    ]
    client = AsyncMock()

    async def folder_status(folder: str) -> dict[str, int]:
        if folder == "Deleted":
            raise ImapFolderError("not accessible")
        return {"messages": 4, "unseen": 2, "uidvalidity": 7, "uidnext": 11}

    async def count_emails(folder: str, _tokens: list[str]):
        if folder == "Deleted":
            raise ImapFolderError("not accessible")
        return 3, "10"

    client.get_folder_status.side_effect = folder_status
    client.count_emails.side_effect = count_emails
    client.get_email_metadata.return_value = _message("10")

    data = await coordinator._async_fetch_data(client)  # noqa: SLF001

    assert data.custom_counts["missing"].count is None
    assert data.custom_counts["healthy"].count == 3
    assert data.custom_counts["healthy"].newest_uid == "10"


@pytest.mark.asyncio
async def test_healthy_watch_folder_continues_when_another_is_unavailable() -> None:
    """A healthy secondary folder still detects and matches arrivals."""
    coordinator = _coordinator()
    healthy_watch = {"id": "healthy", "folder": "Receipts", "filters": {}}
    coordinator.email_watches = [
        {"id": "missing", "folder": "Deleted", "filters": {}},
        healthy_watch,
    ]
    coordinator._folder_uid_state["Receipts"] = (8, 40)  # noqa: SLF001
    coordinator._watch_uid_state["healthy"] = (  # noqa: SLF001
        watch_definition_fingerprint(healthy_watch),
        8,
        40,
    )
    client = AsyncMock()

    async def folder_status(folder: str) -> dict[str, int]:
        if folder == "Deleted":
            raise ImapFolderError("not accessible")
        if folder == "Receipts":
            return {"messages": 1, "unseen": 1, "uidvalidity": 8, "uidnext": 42}
        return {"messages": 4, "unseen": 2, "uidvalidity": 7, "uidnext": 11}

    client.get_folder_status.side_effect = folder_status
    client.get_new_emails.return_value = ([_message("41")], 1)
    client.matching_uids.return_value = {"41"}

    data = await coordinator._async_fetch_data(client)  # noqa: SLF001

    assert [(watch_id, message["uid"]) for watch_id, message in data.watch_matches] == [
        ("healthy", "41")
    ]
    client.matching_uids.assert_awaited_once_with("Receipts", ["41"], ["ALL"])


@pytest.mark.asyncio
async def test_main_monitored_folder_failure_is_not_swallowed() -> None:
    """The primary monitored folder retains the coordinator's failure semantics."""
    coordinator = _coordinator()
    client = AsyncMock()
    client.get_folder_status.side_effect = ImapFolderError("not accessible")

    with pytest.raises(ImapFolderError):
        await coordinator._async_fetch_data(client)  # noqa: SLF001


@pytest.mark.asyncio
async def test_newest_metadata_is_deduplicated_per_refresh() -> None:
    """Sensors sharing one folder/UID reuse the same bounded header fetch."""
    coordinator = _coordinator()
    coordinator.custom_sensors = [
        {"id": "one", "folder": "INBOX", "filters": {"from": "one"}},
        {"id": "two", "folder": "INBOX", "filters": {"from": "two"}},
    ]
    client = AsyncMock()
    client.get_folder_status.return_value = {
        "messages": 1,
        "unseen": 1,
        "uidvalidity": 7,
        "uidnext": 56,
    }
    client.count_emails.return_value = (1, "55")
    client.get_email_metadata.return_value = _message("55")

    data = await coordinator._async_fetch_data(client)  # noqa: SLF001

    assert data.custom_counts["one"].newest_uid == "55"
    assert data.custom_counts["two"].newest_uid == "55"
    client.get_email_metadata.assert_awaited_once_with("INBOX", "55")


def test_watch_payload_is_body_free_and_account_scoped() -> None:
    """Watch delivery includes identity and safe metadata but never content."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {"id": "rsa", "name": "RSA emails", "folder": "INBOX", "filters": {}}
    ]
    received: list[dict] = []
    coordinator.async_add_watch_listener("rsa", received.append)

    coordinator._notify_watch_matches(  # noqa: SLF001
        [("rsa", _message("44")), ("rsa", _message("45"))]
    )

    assert received[0]["account"] == "user@example.com"
    assert received[0]["watch_id"] == "rsa"
    assert received[0]["watch_name"] == "RSA emails"
    assert received[0]["uid"] == "44"
    assert [event["uid"] for event in received] == ["44", "45"]
    assert "plain_text_body" not in received[0]
    assert "body" not in received[0]


def test_one_arrival_can_notify_multiple_watch_entities() -> None:
    """Each matching watch receives its own event for the same new message."""
    coordinator = _coordinator()
    coordinator.email_watches = [
        {"id": "sender", "name": "Sender", "folder": "INBOX"},
        {"id": "pdf", "name": "PDF", "folder": "INBOX"},
    ]
    sender_events: list[dict] = []
    pdf_events: list[dict] = []
    coordinator.async_add_watch_listener("sender", sender_events.append)
    coordinator.async_add_watch_listener("pdf", pdf_events.append)

    coordinator._notify_watch_matches(  # noqa: SLF001
        [("sender", _message("44")), ("pdf", _message("44"))]
    )

    assert [event["uid"] for event in sender_events] == ["44"]
    assert [event["uid"] for event in pdf_events] == ["44"]


def test_watch_listeners_are_isolated_between_accounts() -> None:
    """Equal watch IDs on separate account coordinators cannot cross-deliver."""
    first = _coordinator("first@example.com")
    second = _coordinator("second@example.com")
    definition = {"id": "same-id", "name": "RSA", "folder": "INBOX"}
    first.email_watches = [definition]
    second.email_watches = [definition]
    first_events: list[dict] = []
    second_events: list[dict] = []
    first.async_add_watch_listener("same-id", first_events.append)
    second.async_add_watch_listener("same-id", second_events.append)

    first._notify_watch_matches([("same-id", _message("44"))])  # noqa: SLF001

    assert first_events[0]["account"] == "first@example.com"
    assert second_events == []


@pytest.mark.asyncio
async def test_optional_folder_missing_is_local_rule_failure() -> None:
    """A genuinely unavailable optional folder stays isolated to that rule."""
    coordinator = _coordinator()
    client = AsyncMock()
    client.get_folder_status.side_effect = ImapFolderError("missing")

    result = await coordinator._async_folder_status(  # noqa: SLF001
        client, "Receipts", required=False
    )

    assert result is None


@pytest.mark.asyncio
async def test_optional_folder_transient_imap_failure_propagates() -> None:
    """A network/server failure aborts the refresh so normal retry can run."""
    coordinator = _coordinator()
    client = AsyncMock()
    client.get_folder_status.side_effect = ImapClientError("connection dropped")

    with pytest.raises(ImapClientError):
        await coordinator._async_folder_status(  # noqa: SLF001
            client, "Receipts", required=False
        )


@pytest.mark.asyncio
async def test_optional_folder_validation_failure_remains_isolated() -> None:
    """A local invalid-folder value does not masquerade as a connection outage."""
    coordinator = _coordinator()
    client = AsyncMock()
    client.get_folder_status.side_effect = ValueError("invalid folder")

    result = await coordinator._async_folder_status(  # noqa: SLF001
        client, "Receipts", required=False
    )

    assert result is None
