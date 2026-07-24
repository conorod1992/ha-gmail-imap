"""Focused test bootstrap that does not require a full Home Assistant install."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

_ROOT = Path(__file__).parents[1]
_CUSTOM_COMPONENTS = _ROOT / "custom_components"
_INTEGRATION = _CUSTOM_COMPONENTS / "email_ha"

if "custom_components" not in sys.modules:
    custom_components = ModuleType("custom_components")
    custom_components.__path__ = [str(_CUSTOM_COMPONENTS)]
    sys.modules["custom_components"] = custom_components

if "custom_components.email_ha" not in sys.modules:
    email_ha = ModuleType("custom_components.email_ha")
    email_ha.__path__ = [str(_INTEGRATION)]
    sys.modules["custom_components.email_ha"] = email_ha
