"""Tests for structured, injection-safe IMAP search translation."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.email_ha.search import (
    build_structured_search_tokens,
    normalize_structured_filters,
    quote_imap_search_value,
    summarize_structured_filters,
)


def test_empty_structured_search_is_all() -> None:
    """An empty form produces a valid all-message search."""
    assert build_structured_search_tokens({}) == ["ALL"]


def test_structured_filters_combine_with_and_semantics() -> None:
    """Every populated ordinary field becomes an adjacent IMAP search key."""
    assert build_structured_search_tokens(
        {
            "from": "notifications@example.com",
            "to": "home@example.com",
            "cc": "accounts@example.com",
            "subject": "booking",
            "body": "confirmation",
            "text": "Dublin",
            "read_state": "unread",
            "starred_state": "not_starred",
            "since": "2026-07-01",
            "before": date(2026, 8, 1),
            "on": "2026-07-24",
        }
    ) == [
        "FROM",
        '"notifications@example.com"',
        "TO",
        '"home@example.com"',
        "CC",
        '"accounts@example.com"',
        "SUBJECT",
        '"booking"',
        "BODY",
        '"confirmation"',
        "TEXT",
        '"Dublin"',
        "UNSEEN",
        "UNFLAGGED",
        "SINCE",
        "01-Jul-2026",
        "BEFORE",
        "01-Aug-2026",
        "ON",
        "24-Jul-2026",
    ]


@pytest.mark.parametrize(
    ("read_state", "expected"), [("unread", "UNSEEN"), ("read", "SEEN")]
)
def test_read_state_translation(read_state: str, expected: str) -> None:
    """Friendly read-state values translate to standard IMAP criteria."""
    assert build_structured_search_tokens({"read_state": read_state}) == [expected]


@pytest.mark.parametrize(
    ("starred_state", "expected"),
    [("starred", "FLAGGED"), ("not_starred", "UNFLAGGED")],
)
def test_starred_state_translation(starred_state: str, expected: str) -> None:
    """Friendly starred-state values translate to standard flags."""
    assert build_structured_search_tokens({"starred_state": starred_state}) == [
        expected
    ]


def test_quotes_and_backslashes_are_escaped() -> None:
    """Structured strings cannot break out of their IMAP quoted argument."""
    assert build_structured_search_tokens({"subject": 'a "quote" \\ path'}) == [
        "SUBJECT",
        '"a \\"quote\\" \\\\ path"',
    ]


@pytest.mark.parametrize("value", ["bad\rvalue", "bad\nvalue", "bad\x00value"])
def test_control_characters_are_rejected(value: str) -> None:
    """Structured values reject IMAP command-injection control characters."""
    with pytest.raises(ValueError, match="control characters"):
        build_structured_search_tokens({"from": value})


def test_invalid_date_is_rejected() -> None:
    """Date fields accept ISO dates only."""
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        build_structured_search_tokens({"since": "01/07/2026"})


def test_gmail_category_and_importance_use_documented_extension() -> None:
    """Gmail-only filters are kept within one safely quoted X-GM-RAW value."""
    assert build_structured_search_tokens(
        {"gmail_category": "primary", "important_state": "important"}
    ) == ["X-GM-RAW", '"category:primary is:important"']


@pytest.mark.parametrize(
    ("state", "term"),
    [("has_attachment", "has:attachment"), ("no_attachment", "-has:attachment")],
)
def test_attachment_state_uses_safe_gmail_search(state: str, term: str) -> None:
    """Attachment presence is expressed through Gmail's structured raw search."""
    assert build_structured_search_tokens({"attachment_state": state}) == [
        "X-GM-RAW",
        f'"{term}"',
    ]


def test_attachment_filename_combines_with_other_filters_and_escapes() -> None:
    """Filename criteria stay safely quoted and AND-combine with normal fields."""
    tokens = build_structured_search_tokens(
        {
            "from": "rsa.ie",
            "attachment_state": "has_attachment",
            "attachment_filename": 'invoice "final".pdf',
        }
    )

    assert tokens[:3] == ["FROM", '"rsa.ie"', "X-GM-RAW"]
    assert tokens[3] == quote_imap_search_value(
        'has:attachment filename:"invoice \\"final\\".pdf"', "Gmail filter"
    )


def test_invalid_attachment_state_is_rejected() -> None:
    """Only the friendly, documented attachment states are accepted."""
    with pytest.raises(ValueError, match="attachment_state"):
        normalize_structured_filters({"attachment_state": "maybe"})


@pytest.mark.parametrize("value", ["bad\rname", "bad\nname", "bad\x00name"])
def test_attachment_filename_rejects_command_controls(value: str) -> None:
    """Filename input retains the shared IMAP injection protections."""
    with pytest.raises(ValueError, match="control characters"):
        build_structured_search_tokens({"attachment_filename": value})


def test_full_summary_includes_every_supported_filter_in_plain_language() -> None:
    """The deterministic full summary never drops an active rule condition."""
    summary = summarize_structured_filters(
        {
            "from": "sender.example",
            "to": "home@example.com",
            "cc": "accounts@example.com",
            "subject": "renewal",
            "body": "reference",
            "text": "Dublin",
            "read_state": "unread",
            "gmail_category": "primary",
            "important_state": "not_important",
            "starred_state": "not_starred",
            "attachment_state": "has_attachment",
            "attachment_filename": "invoice.pdf",
            "since": "2026-01-01",
            "before": "2026-12-31",
            "on": "2026-08-26",
        },
        folder="INBOX",
    )

    for expected in (
        "Inbox",
        "Unread",
        "Category Primary",
        "Not important",
        "Not starred",
        "Has attachment",
        'From contains "sender.example"',
        'To contains "home@example.com"',
        'Cc contains "accounts@example.com"',
        'Subject contains "renewal"',
        'Body contains "reference"',
        'Text contains "Dublin"',
        'Attachment name contains "invoice.pdf"',
        "Since 2026-01-01",
        "Before 2026-12-31",
        "On 2026-08-26",
    ):
        assert expected in summary
    assert "not_starred" not in summary
    assert "not_important" not in summary


def test_short_summary_truncates_without_changing_full_summary() -> None:
    """List labels stay compact while the full representation remains complete."""
    filters = {
        "from": "one",
        "to": "two",
        "subject": "three",
        "body": "four",
        "text": "five",
    }

    assert "+2 more" in summarize_structured_filters(
        filters, folder="INBOX", short=True
    )
    assert "+" not in summarize_structured_filters(filters, folder="INBOX")
