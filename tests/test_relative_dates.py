"""Tests for rolling structured date filters."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.email_ha.search import (
    build_structured_search_tokens,
    normalize_structured_filters,
    summarize_structured_filters,
)


def test_today_uses_local_calendar_day() -> None:
    assert build_structured_search_tokens(
        {"relative_date": "today"}, current_date=date(2026, 8, 27)
    ) == ["SINCE", "27-Aug-2026"]


def test_yesterday_is_one_calendar_day() -> None:
    assert build_structured_search_tokens(
        {"relative_date": "yesterday"}, current_date=date(2026, 8, 27)
    ) == ["SINCE", "26-Aug-2026", "BEFORE", "27-Aug-2026"]


@pytest.mark.parametrize(
    ("value", "term"),
    [
        ("last_24_hours", "newer_than:1d"),
        ("last_7_days", "newer_than:7d"),
        ("last_30_days", "newer_than:30d"),
    ],
)
def test_rolling_ranges_use_gmail_server_search(value: str, term: str) -> None:
    assert build_structured_search_tokens({"relative_date": value}) == [
        "X-GM-RAW",
        f'"{term}"',
    ]


def test_relative_date_combines_with_other_gmail_terms() -> None:
    assert build_structured_search_tokens(
        {
            "relative_date": "last_7_days",
            "gmail_category": "primary",
            "attachment_state": "has_attachment",
        }
    ) == [
        "X-GM-RAW",
        '"newer_than:7d category:primary has:attachment"',
    ]


def test_relative_and_exact_dates_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="Relative date"):
        normalize_structured_filters(
            {"relative_date": "last_7_days", "since": "2026-08-01"}
        )


def test_invalid_relative_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="relative_date"):
        normalize_structured_filters({"relative_date": "last_forever"})


def test_relative_date_summary_is_human_readable() -> None:
    summary = summarize_structured_filters(
        {"relative_date": "last_30_days"}, folder="INBOX"
    )
    assert summary == "Inbox · Received in the last 30 days"
