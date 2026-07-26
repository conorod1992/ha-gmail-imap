"""Tests for optional search-sensor form validation."""

from __future__ import annotations

from typing import Any, cast

import pytest
import voluptuous as vol

from custom_components.email_ha.config_flow import _search_sensor_schema


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
