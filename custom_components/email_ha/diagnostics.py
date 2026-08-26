"""Privacy-conscious diagnostics for Email HA."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import (
    CONF_CUSTOM_SENSORS,
    CONF_EMAIL,
    CONF_EMAIL_WATCHES,
    CONF_MONITORED_FOLDER,
)
from .coordinator import coordinator_from_entry


def _redact_email(value: str) -> str:
    """Keep enough account identity for support without exposing an address."""
    local, separator, domain = value.partition("@")
    return f"{local[:1]}***{separator}{domain}" if separator else "***"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return useful operational state without credentials or rule values."""
    integration = await async_get_integration(hass, entry.domain)
    watches = list(entry.options.get(CONF_EMAIL_WATCHES, []))
    coordinator = coordinator_from_entry(hass, entry.entry_id)
    return {
        "integration_version": integration.version,
        "account": _redact_email(str(entry.data.get(CONF_EMAIL, ""))),
        "monitored_folder": entry.options.get(CONF_MONITORED_FOLDER, "INBOX"),
        "enabled_gmail_entities": sorted(coordinator.enabled_gmail_entities)
        if coordinator
        else [],
        "custom_sensor_count": len(entry.options.get(CONF_CUSTOM_SENSORS, [])),
        "email_watch_count": len(watches),
        "enabled_watch_count": sum(watch.get("enabled", True) for watch in watches),
        "disabled_watch_count": sum(
            not watch.get("enabled", True) for watch in watches
        ),
        "last_successful_update": getattr(coordinator, "last_success_time", None),
        "idle_running": coordinator.idle_running if coordinator else False,
        "cached_folder_count": coordinator.cached_folder_count if coordinator else 0,
        "coordinator_last_update_success": getattr(
            coordinator, "last_update_success", None
        ),
        "coordinator_last_exception": (
            type(getattr(coordinator, "last_exception", None)).__name__
            if getattr(coordinator, "last_exception", None)
            else None
        ),
        "event_baseline_ready": coordinator.event_baseline_ready
        if coordinator
        else False,
    }
