"""Tests for action metadata and progressive-disclosure sections."""

from __future__ import annotations

from pathlib import Path

import yaml


def _services() -> dict:
    path = (
        Path(__file__).parents[1] / "custom_components" / "email_ha" / "services.yaml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _flatten_fields(fields: dict) -> dict:
    flattened = {}
    for key, value in fields.items():
        if "fields" in value and "selector" not in value:
            flattened.update(_flatten_fields(value["fields"]))
        else:
            flattened[key] = value
    return flattened


def test_only_three_actions_are_documented() -> None:
    """Removed action aliases do not remain discoverable."""
    services = _services()
    assert set(services) == {
        "find_emails",
        "search_emails",
        "get_email_contents",
    }
    assert all(action["target"] == {} for action in services.values())


def test_find_email_fields_have_descriptions_and_safe_selectors() -> None:
    """The UI explains common, advanced, and private-content choices."""
    fields = _flatten_fields(_services()["find_emails"]["fields"])

    assert all(isinstance(field, str) for field in fields)
    assert "on" in fields
    for field in ("from", "subject", "body", "text", "folder", "include_body"):
        assert fields[field]["description"]
        assert "selector" in fields[field]
    assert "private" in fields["include_body"]["description"].lower()
    assert fields["include_body"]["default"] is False


def test_raw_action_is_unambiguously_advanced() -> None:
    """Normal users are directed toward Find emails."""
    action = _services()["search_emails"]

    assert action["name"].startswith("Advanced:")
    assert "Find emails" in action["description"]
