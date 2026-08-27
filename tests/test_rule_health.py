# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false
"""Tests for privacy-safe per-rule health transitions."""

from __future__ import annotations

from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator
from custom_components.email_ha.imap_client import ImapClientError


def _coordinator() -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._rule_health = {}
    return coordinator


def test_rule_health_retains_last_error_after_recovery() -> None:
    """A recovered rule is healthy while keeping useful historical failure context."""
    coordinator = _coordinator()

    coordinator._set_rule_success("rule-1", checked_at="2026-08-27T20:00:00+00:00")
    coordinator._set_rule_error(
        "rule-1", ImapClientError("private server text"), "Email watch query failed"
    )
    failed = coordinator.rule_health("rule-1")

    assert failed.status == "Error"
    assert failed.last_successful_check == "2026-08-27T20:00:00+00:00"
    assert failed.last_error_type == "ImapClientError"
    assert failed.last_error == "Email watch query failed"
    assert "private server text" not in str(failed)

    coordinator._set_rule_success("rule-1", checked_at="2026-08-27T21:00:00+00:00")
    recovered = coordinator.rule_health("rule-1")

    assert recovered.status == "Healthy"
    assert recovered.last_successful_check == "2026-08-27T21:00:00+00:00"
    assert recovered.last_error_type == "ImapClientError"
    assert recovered.last_error == "Email watch query failed"


def test_paused_and_folder_error_states_are_distinct() -> None:
    """A disabled watch is not presented as a failure."""
    coordinator = _coordinator()

    coordinator._set_rule_paused("watch-1")
    assert coordinator.rule_health("watch-1").status == "Paused"

    coordinator._set_rule_folder_error("watch-1")
    health = coordinator.rule_health("watch-1")
    assert health.status == "Error"
    assert health.last_error_type == "FolderQueryError"
    assert health.last_error == "Configured folder could not be queried"


def test_unknown_rule_has_explicit_unknown_health() -> None:
    """Entities have a stable state before the first coordinator refresh."""
    coordinator = _coordinator()

    health = coordinator.rule_health("missing")

    assert health.status == "Unknown"
    assert health.last_successful_check is None
    assert health.last_error_at is None
