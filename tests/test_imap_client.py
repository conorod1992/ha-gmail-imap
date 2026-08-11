"""Tests for read-only IMAP commands and search validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.imap_client import ImapClient, tokenize_search_criteria


@dataclass
class _Response:
    result: str
    lines: list[Any]


@pytest.mark.parametrize(
    ("criteria", "tokens"),
    [
        ("ALL", ["ALL"]),
        ('FROM "person@example.com"', ["FROM", '"person@example.com"']),
        ('SUBJECT "annual renewal"', ["SUBJECT", '"annual renewal"']),
        (
            'UNSEEN FROM "person@example.com"',
            ["UNSEEN", "FROM", '"person@example.com"'],
        ),
        ("SINCE 01-Jul-2026", ["SINCE", "01-Jul-2026"]),
    ],
)
def test_search_criteria_preserve_quoted_values(
    criteria: str, tokens: list[str]
) -> None:
    """Quoted search values remain a single IMAP command argument."""
    assert tokenize_search_criteria(criteria) == tokens


@pytest.mark.parametrize(
    "criteria", ["", "  ", 'SUBJECT "unterminated', "ALL\r\nLOGOUT"]
)
def test_reject_malformed_search_criteria(criteria: str) -> None:
    """Reject empty, unmatched, and control-character criteria."""
    with pytest.raises(ValueError):
        tokenize_search_criteria(criteria)


@pytest.mark.asyncio
async def test_search_uses_read_only_header_peek() -> None:
    """Metadata search uses EXAMINE/read-only and does not fetch a body."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"1"])
    protocol.uid_search.return_value = _Response("OK", [b"42"])
    protocol.uid.return_value = _Response(
        "OK",
        [
            b'1 (UID 42 FLAGS () INTERNALDATE "24-Jul-2026 15:30:01 +0100")',
            b"From: sender@example.com\r\nSubject: Example\r\n\r\n",
            b")",
        ],
    )
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    result = await client.search_emails("INBOX", "ALL", 10)

    protocol.examine.assert_awaited_once_with("INBOX")
    protocol.uid.assert_awaited_once_with(
        "fetch", "42", "(BODY.PEEK[HEADER]<0.65536> FLAGS INTERNALDATE)"
    )
    assert result[0]["uid"] == "42"
    assert "plain_text_body" not in result[0]


@pytest.mark.asyncio
async def test_search_include_body_uses_body_peek_and_limit() -> None:
    """The raw search include-body flag reaches FETCH and the MIME parser."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"1"])
    protocol.uid_search.return_value = _Response("OK", [b"42"])
    protocol.uid.return_value = _Response(
        "OK",
        [
            b'1 (UID 42 FLAGS () INTERNALDATE "24-Jul-2026 15:30:01 +0100")',
            (
                b"From: sender@example.com\r\nSubject: Example\r\n"
                b"Content-Type: text/plain\r\n\r\nLong body"
            ),
            b")",
        ],
    )
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    result = await client.search_emails(
        "INBOX", "ALL", 10, include_body=True, body_max_chars=4
    )

    protocol.uid.assert_awaited_once_with(
        "fetch", "42", "(BODY.PEEK[]<0.2000000> FLAGS INTERNALDATE)"
    )
    assert result[0]["plain_text_body"] == "Long"
    assert result[0]["body_truncated"] is True


@pytest.mark.asyncio
async def test_structured_search_include_body_uses_same_fetch_path() -> None:
    """Pre-tokenized structured search shares the raw search body implementation."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"1"])
    protocol.uid_search.return_value = _Response("OK", [b"7"])
    protocol.uid.return_value = _Response(
        "OK",
        [b"1 (UID 7 FLAGS ())", b"Subject: Example\r\n\r\nBody", b")"],
    )
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    result = await client.search_emails_tokens(
        "INBOX", ["UNSEEN"], include_body=True, body_max_chars=100
    )

    protocol.uid_search.assert_awaited_once_with("UNSEEN", charset=None)
    assert result[0]["plain_text_body"] == "Body"


@pytest.mark.asyncio
async def test_count_search_does_not_fetch_messages() -> None:
    """Count sensors use only server-side UID SEARCH and never fetch bodies."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"3"])
    protocol.uid_search.return_value = _Response("OK", [b"3 5 8"])
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    count, newest_uid = await client.count_emails("INBOX", ["UNSEEN"])

    assert (count, newest_uid) == (3, "8")
    protocol.uid.assert_not_awaited()


@pytest.mark.asyncio
async def test_uid_match_is_bounded_to_one_new_message() -> None:
    """Watch matching adds an exact UID constraint to the shared filter tokens."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"1"])
    protocol.uid_search.return_value = _Response("OK", [b"44"])
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    assert await client.uid_matches("INBOX", "44", ["FROM", '"rsa.ie"'])
    protocol.uid_search.assert_awaited_once_with(
        "UID", "44", "FROM", '"rsa.ie"', charset=None
    )


@pytest.mark.asyncio
async def test_folder_control_characters_are_rejected() -> None:
    """Folder input cannot inject another IMAP command."""
    protocol = AsyncMock()
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    with pytest.raises(ValueError, match="control characters"):
        await client.count_emails("INBOX\r\nLOGOUT", ["ALL"])

    protocol.examine.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_email_contents_uses_read_only_body_peek() -> None:
    """Explicit retrieval uses PEEK and preserves the unread state."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"1"])
    protocol.uid.return_value = _Response(
        "OK",
        [
            b'1 (UID 7 FLAGS (\\Seen) INTERNALDATE "24-Jul-2026 15:30:01 +0100")',
            b"From: sender@example.com\r\nSubject: Example\r\n\r\nBody",
            b")",
        ],
    )
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    result = await client.get_email_contents("INBOX", "7", body_max_chars=100)

    protocol.examine.assert_awaited_once_with("INBOX")
    protocol.uid.assert_awaited_once_with(
        "fetch", "7", "(BODY.PEEK[]<0.2000000> FLAGS INTERNALDATE)"
    )
    assert result["plain_text_body"] == "Body"
    assert result["flags"] == [r"\Seen"]
    assert result["body_truncated"] is False


@pytest.mark.asyncio
async def test_folder_status_includes_uid_generation_metadata() -> None:
    """UIDVALIDITY and UIDNEXT support safe event baselines."""
    protocol = AsyncMock()
    protocol.status.return_value = _Response(
        "OK", [b'"INBOX" (MESSAGES 4 UNSEEN 2 UIDVALIDITY 77 UIDNEXT 101)']
    )
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    status = await client.get_folder_status("INBOX")

    assert status == {
        "messages": 4,
        "unseen": 2,
        "uidvalidity": 77,
        "uidnext": 101,
    }
    protocol.status.assert_awaited_once_with(
        "INBOX", "(MESSAGES UNSEEN UIDVALIDITY UIDNEXT)"
    )


@pytest.mark.asyncio
async def test_new_email_fetch_is_bounded_and_uid_ordered() -> None:
    """Large bursts emit the newest bounded window from oldest UID to newest."""
    protocol = AsyncMock()
    protocol.examine.return_value = _Response("OK", [b"30"])
    protocol.uid_search.return_value = _Response(
        "OK", [" ".join(str(uid) for uid in range(11, 41)).encode()]
    )
    protocol.uid.side_effect = [
        _Response("OK", [f"1 (UID {uid})".encode(), f"Subject: {uid}\r\n\r\n".encode()])
        for uid in range(16, 41)
    ]
    client = ImapClient("imap.gmail.com")
    client._client = protocol  # noqa: SLF001

    messages, total = await client.get_new_emails("INBOX", 10, 25)

    assert total == 30
    assert [message["uid"] for message in messages] == [
        str(uid) for uid in range(16, 41)
    ]
    protocol.uid_search.assert_awaited_once_with("UID", "11:*", charset=None)
