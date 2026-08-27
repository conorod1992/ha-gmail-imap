"""Repair issue helpers for Email HA."""

from __future__ import annotations

from hashlib import sha256

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _folder_issue_id(entry: ConfigEntry, folder: str) -> str:
    """Return a stable non-sensitive issue id for one configured folder."""
    digest = sha256(folder.encode()).hexdigest()[:12]
    return f"folder_unavailable_{entry.entry_id}_{digest}"


def report_folder_unavailable(
    hass: HomeAssistant, entry: ConfigEntry, folder: str
) -> None:
    """Tell the user when a configured folder no longer exists/is accessible."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _folder_issue_id(entry, folder),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="folder_unavailable",
        translation_placeholders={"folder": folder},
    )


def clear_folder_unavailable(
    hass: HomeAssistant, entry: ConfigEntry, folder: str
) -> None:
    """Clear a folder issue as soon as Gmail reports it accessible again."""
    ir.async_delete_issue(hass, DOMAIN, _folder_issue_id(entry, folder))
