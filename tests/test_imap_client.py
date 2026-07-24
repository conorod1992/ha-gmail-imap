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
async def test_get_message_uses_read_only_body_peek() -> None:
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

    result = await client.get_message("INBOX", "7", body_max_chars=100)

    protocol.examine.assert_awaited_once_with("INBOX")
    protocol.uid.assert_awaited_once_with(
        "fetch", "7", "(BODY.PEEK[]<0.2000000> FLAGS INTERNALDATE)"
    )
    assert result["plain_text_body"] == "Body"
    assert result["flags"] == [r"\Seen"]
    assert result["body_truncated"] is False
