"""Tests for translation and release metadata consistency."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from custom_components.email_ha.gmail import GMAIL_ENTITY_DEFINITIONS

_ROOT = Path(__file__).parents[1]
_INTEGRATION = _ROOT / "custom_components" / "email_ha"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_custom_integration_english_translation_is_complete() -> None:
    """Runtime English translations mirror the canonical source exactly."""
    assert _json(_INTEGRATION / "translations" / "en.json") == _json(
        _INTEGRATION / "strings.json"
    )


def test_every_fixed_entity_uses_a_translation_key() -> None:
    """No built-in entity name relies on hard-coded Python text."""
    strings = _json(_INTEGRATION / "strings.json")["entity"]
    expected_sensors = {
        definition.key
        for definition in GMAIL_ENTITY_DEFINITIONS
        if definition.platform == "sensor"
    }
    expected_events = {
        definition.key
        for definition in GMAIL_ENTITY_DEFINITIONS
        if definition.platform == "event"
    }

    assert set(strings["sensor"]) == expected_sensors
    assert set(strings["event"]) == expected_events


def test_action_translation_and_yaml_surfaces_match() -> None:
    """Only the clean three-action API is translated and discoverable."""
    strings = _json(_INTEGRATION / "strings.json")
    services = yaml.safe_load(
        (_INTEGRATION / "services.yaml").read_text(encoding="utf-8")
    )

    assert (
        set(strings["services"])
        == set(services)
        == {
            "find_emails",
            "search_emails",
            "get_email_contents",
        }
    )


def test_breaking_redesign_has_matching_release_metadata() -> None:
    """The clean first public API is released as 2.0 with current HA minimum."""
    assert _json(_INTEGRATION / "manifest.json")["version"] == "2.1.0"
    assert _json(_ROOT / "hacs.json")["homeassistant"] == "2026.7.0"
