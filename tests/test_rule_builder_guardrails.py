"""Regression tests for rule-builder summaries, dates, and safety guidance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.email_ha.search import (
    build_structured_search_tokens,
    normalize_structured_filters,
    summarize_structured_filters,
)


def test_filterless_rule_summary_names_the_folder_and_scope() -> None:
    """An empty rule must say that it covers every email, not only name the folder."""
    assert summarize_structured_filters({}, folder="INBOX") == "All email in Inbox"
    assert (
        summarize_structured_filters({}, folder="Projects/Invoices")
        == "All email in Projects/Invoices"
    )
    assert summarize_structured_filters({}) == "All email"


@pytest.mark.parametrize(
    "filters",
    [
        {"since": "2026-09-10", "before": "2026-09-10"},
        {"since": "2026-09-11", "before": "2026-09-10"},
        {"since": "2026-09-10", "on": "2026-09-09"},
        {"before": "2026-09-10", "on": "2026-09-10"},
        {"before": "2026-09-10", "on": "2026-09-11"},
    ],
)
def test_impossible_every_condition_date_ranges_are_rejected(
    filters: dict[str, str],
) -> None:
    """AND date filters that cannot overlap fail before they reach IMAP."""
    with pytest.raises(ValueError):
        normalize_structured_filters(filters)


def test_valid_exact_date_range_remains_supported() -> None:
    """An On date inside inclusive-Since/exclusive-Before boundaries is valid."""
    assert normalize_structured_filters(
        {
            "since": "2026-09-01",
            "before": "2026-10-01",
            "on": "2026-09-15",
        }
    ) == {
        "since": "2026-09-01",
        "before": "2026-10-01",
        "on": "2026-09-15",
    }


def test_match_any_dates_are_not_mistaken_for_an_impossible_range() -> None:
    """With OR semantics, individually valid date clauses need not overlap."""
    tokens = build_structured_search_tokens(
        {
            "match_mode": "any",
            "since": "2026-09-10",
            "before": "2026-09-05",
        }
    )

    assert tokens[0] == "OR"
    assert any("SINCE 10-Sep-2026" in token for token in tokens)
    assert any("BEFORE 05-Sep-2026" in token for token in tokens)


def test_rule_builder_copy_warns_about_broad_rules_and_explains_catch_up() -> None:
    """The English UI gives explicit guardrails without adding another form step."""
    root = Path(__file__).parents[1] / "custom_components" / "email_ha"
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    translations = json.loads(
        (root / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == translations

    watch = strings["options"]["step"]["email_watch_common"]
    assert "fires for every newly observed email" in watch["description"]
    assert "may then fire for most new email" in watch["data_description"]["match_mode"]
    assert "watch's own last-seen point" in watch["data_description"]["catch_up"]
    assert "does not backfill email from before the watch was created" in watch[
        "data_description"
    ]["catch_up"]
