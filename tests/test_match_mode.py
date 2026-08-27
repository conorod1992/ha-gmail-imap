"""Tests for Match all / Match any structured filters."""

# ruff: noqa: D103

from __future__ import annotations

from datetime import date

import pytest

from custom_components.email_ha import FIND_EMAILS_SCHEMA
from custom_components.email_ha.config_flow import _custom_common_schema
from custom_components.email_ha.search import (
    build_structured_search_tokens,
    normalize_structured_filters,
    summarize_structured_filters,
)


def test_legacy_filters_still_default_to_all() -> None:
    assert build_structured_search_tokens({"from": "rsa.ie", "subject": "renewal"}) == [
        "FROM",
        '"rsa.ie"',
        "SUBJECT",
        '"renewal"',
    ]


def test_match_any_builds_nested_imap_or() -> None:
    assert build_structured_search_tokens(
        {"match_mode": "any", "from": "rsa.ie", "subject": "check test"}
    ) == ["OR", '(FROM "rsa.ie")', '(SUBJECT "check test")']


def test_match_any_nests_three_independent_conditions() -> None:
    assert build_structured_search_tokens(
        {
            "match_mode": "any",
            "from": "rsa.ie",
            "subject": "renewal",
            "read_state": "unread",
        }
    ) == [
        "OR",
        '(FROM "rsa.ie")',
        '(OR (SUBJECT "renewal") UNSEEN)',
    ]


def test_match_any_handles_single_token_and_gmail_extension() -> None:
    assert build_structured_search_tokens(
        {"match_mode": "any", "read_state": "unread", "gmail_category": "primary"}
    ) == ["OR", "UNSEEN", '(X-GM-RAW "category:primary")']


def test_match_any_keeps_multi_key_date_window_as_one_condition() -> None:
    assert build_structured_search_tokens(
        {"match_mode": "any", "relative_date": "yesterday", "from": "rsa.ie"},
        current_date=date(2026, 8, 27),
    ) == ["OR", '(FROM "rsa.ie")', "(SINCE 26-Aug-2026 BEFORE 27-Aug-2026)"]


def test_match_any_with_no_filled_filters_remains_all_email() -> None:
    assert build_structured_search_tokens({"match_mode": "any"}) == ["ALL"]


def test_invalid_match_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="match_mode"):
        normalize_structured_filters({"match_mode": "some"})


def test_match_any_is_visible_in_summary() -> None:
    assert summarize_structured_filters(
        {"match_mode": "any", "from": "rsa.ie", "subject": "renewal"}, folder="INBOX"
    ).startswith("Match any condition · Inbox")


def test_find_emails_schema_accepts_match_any() -> None:
    assert FIND_EMAILS_SCHEMA({"match_mode": "any"})["match_mode"] == "any"


def test_options_common_form_exposes_match_mode() -> None:
    schema = _custom_common_schema(["INBOX"], {})
    assert "match_mode" in {getattr(field, "schema", field) for field in schema.schema}
