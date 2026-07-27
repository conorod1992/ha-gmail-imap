"""Tests for optional search-sensor form validation."""

from __future__ import annotations

from typing import Any, cast

import pytest
import voluptuous as vol

from custom_components.email_ha.config_flow import (
    _gmail_sensor_preset_schema,
    _search_sensor_schema,
)
from custom_components.email_ha.search import (
    GMAIL_INBOX_SENSOR_PRESETS,
    build_structured_search_tokens,
)


def test_search_sensor_form_defaults_are_privacy_conscious() -> None:
    """A new count sensor defaults to INBOX and no body-return behavior."""
    result = cast(dict[str, Any], _search_sensor_schema()({"name": "RSA unread"}))

    assert result["folder"] == "INBOX"
    assert result["read_state"] == "any"
    assert result["starred_state"] == "any"
    assert result["important_state"] == "any"
    assert result["gmail_category"] == "any"
    assert "include_body" not in result


def test_search_sensor_form_rejects_unknown_category() -> None:
    """Only Gmail's known inbox categories can be selected."""
    with pytest.raises(vol.Invalid):
        _search_sensor_schema()(
            {"name": "Unknown category", "gmail_category": "reservations"}
        )


def test_gmail_preset_form_accepts_multiple_optional_sensors() -> None:
    """Users can explicitly select several useful presets at once."""
    result = cast(
        dict[str, Any],
        _gmail_sensor_preset_schema()(
            {"presets": ["primary_unread", "important_unread"]}
        ),
    )

    assert result["presets"] == ["primary_unread", "important_unread"]


@pytest.mark.parametrize(
    ("preset", "tokens"),
    [
        (
            "primary_unread",
            ["UNSEEN", "X-GM-RAW", '"category:primary"'],
        ),
        (
            "important_unread",
            ["UNSEEN", "X-GM-RAW", '"is:important"'],
        ),
        ("starred_unread", ["UNSEEN", "FLAGGED"]),
    ],
)
def test_gmail_preset_uses_documented_server_side_query(
    preset: str, tokens: list[str]
) -> None:
    """Convenience sensors map to the intended IMAP/Gmail criteria."""
    assert (
        build_structured_search_tokens(GMAIL_INBOX_SENSOR_PRESETS[preset]["filters"])
        == tokens
    )
