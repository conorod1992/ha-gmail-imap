"""Privacy-conscious diagnostics for Email HA."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CUSTOM_SENSORS, CONF_EMAIL, CONF_EMAIL_WATCHES, CONF_MONITORED_FOLDER, DOMAIN
from .coordinator import coordinator_from_entry


def _redact_email(value: str) -> str:
    """Keep enough account identity for support without exposing an address."""
    local, separator, domain = value.partition("@")
    return f"{local[:1]}***{separator}{domain}" if separator else "***"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return useful operational state without credentials or rule values."""
    watches = list(entry.options.get(CONF_EMAIL_WATCHES, []))
    coordinator = coordinator_from_entry(hass, entry.entry_id)
    return {
        "account": _redact_email(str(entry.data.get(CONF_EMAIL, ""))),
        "monitored_folder": entry.options.get(CONF_MONITORED_FOLDER, "INBOX"),
        "enabled_gmail_entities": sorted(getattr(coordinator, "enabled_gmail_entities", set())),
        "custom_sensor_count": len(entry.options.get(CONF_CUSTOM_SENSORS, [])),
        "email_watch_count": len(watches),
        "enabled_watch_count": sum(watch.get("enabled", True) for watch in watches),
        "disabled_watch_count": sum(not watch.get("enabled", True) for watch in watches),
        "last_successful_update": getattr(coordinator, "last_success_time", None),
        "idle_running": bool(getattr(coordinator, "_idle_task", None)),
        "cached_folder_count": len(getattr(coordinator, "_cached_folders", [])),
        "coordinator_last_update_success": getattr(coordinator, "last_update_success", None),
        "coordinator_last_exception": (
            type(getattr(coordinator, "last_exception", None)).__name__
            if getattr(coordinator, "last_exception", None) else None
        ),
        "uid_baseline_ready": getattr(coordinator, "_event_baseline_ready", False),
    }
