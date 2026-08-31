"""Repository metadata and frontend contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).parents[1]
_INTEGRATION = _ROOT / "custom_components" / "email_ha"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_and_hacs_metadata_are_consistent() -> None:
    """The integration remains discoverable and installable."""
    manifest = _json(_INTEGRATION / "manifest.json")
    hacs = _json(_ROOT / "hacs.json")

    assert manifest["domain"] == "email_ha"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_push"
    assert "application_credentials" in manifest["dependencies"]
    assert hacs["content_in_root"] is False
    assert hacs["render_readme"] is True


def test_strings_and_translations_expose_the_same_top_level_contract() -> None:
    """English translations should stay in lock-step with strings.json."""
    strings = _json(_INTEGRATION / "strings.json")
    translation = _json(_INTEGRATION / "translations" / "en.json")

    assert strings.keys() == translation.keys()
    assert strings["config"].keys() == translation["config"].keys()
    assert strings["options"].keys() == translation["options"].keys()
    assert strings["entity"].keys() == translation["entity"].keys()


def test_services_yaml_matches_strings_service_names() -> None:
    """The action UI and translated service labels stay aligned."""
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


def test_release_metadata_matches_current_feature_version() -> None:
    """The manifest exposes the current feature release and supported HA minimum."""
    assert _json(_INTEGRATION / "manifest.json")["version"] == "2.10.1"
    assert _json(_ROOT / "hacs.json")["homeassistant"] == "2026.7.0"
