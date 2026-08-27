"""Focused test bootstrap that does not require a full Home Assistant install."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

_ROOT = Path(__file__).parents[1]
_CUSTOM_COMPONENTS = _ROOT / "custom_components"
if "custom_components" not in sys.modules:
    custom_components = ModuleType("custom_components")
    custom_components.__path__ = [str(_CUSTOM_COMPONENTS)]
    sys.modules["custom_components"] = custom_components

from custom_components.email_ha.coordinator import (  # noqa: E402
    EmailDataUpdateCoordinator,
)


@pytest.fixture(autouse=True)
def _reset_partial_coordinator_rule_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give tests that bypass coordinator __init__ isolated rule-health state."""
    monkeypatch.setattr(EmailDataUpdateCoordinator, "_rule_health", {}, raising=False)
