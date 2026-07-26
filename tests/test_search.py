"""Tests for structured, injection-safe IMAP search translation."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.email_ha.search import build_structured_search_tokens


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
