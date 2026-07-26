"""Tests for Home Assistant service-description YAML compatibility."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_all_service_field_keys_are_strings() -> None:
    """YAML keywords such as `on` must not become boolean mapping keys."""
    services_path = (
        Path(__file__).parents[1] / "custom_components" / "email_ha" / "services.yaml"
    )
    services = yaml.safe_load(services_path.read_text(encoding="utf-8"))

    for service in services.values():
        assert all(isinstance(field, str) for field in service["fields"])
    assert "on" in services["find_emails"]["fields"]
