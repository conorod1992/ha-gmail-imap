"""Shared entity helpers for Email HA."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_EMAIL, DOMAIN


def gmail_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the account-specific Gmail service device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Gmail - {entry.data[CONF_EMAIL]}",
        manufacturer="Google",
        model="Gmail (OAuth2)",
        entry_type=DeviceEntryType.SERVICE,
    )
