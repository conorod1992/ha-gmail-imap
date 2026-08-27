"""Discoverable New email EventEntity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CATCH_UP,
    CONF_EMAIL_WATCHES,
    CONF_FOLDER,
    DEFAULT_FOLDER,
    DOMAIN,
    EVENT_TYPE_NEW_EMAIL,
    EVENT_TYPE_NEW_MATCHING_EMAIL,
)
from .coordinator import EmailDataUpdateCoordinator
from .entity import gmail_device_info
from .gmail import GMAIL_ENTITIES, enabled_entities_for_entry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one account-scoped new-email event entity."""
    coordinator: EmailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            NewEmailEventEntity(coordinator, entry),
            *(
                EmailWatchEventEntity(coordinator, entry, watch)
                for watch in entry.options.get(CONF_EMAIL_WATCHES, [])
            ),
        ]
    )


class NewEmailEventEntity(EventEntity):
    """Expose coordinator notifications as the sole public automation event."""

    _attr_event_types = [EVENT_TYPE_NEW_EMAIL]
    _attr_has_entity_name = True
    _attr_translation_key = "new_email"

    def __init__(
        self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_new_email"
        self._attr_icon = GMAIL_ENTITIES["new_email"].icon
        self._attr_device_info = gmail_device_info(entry)
        self._attr_entity_registry_enabled_default = (
            "new_email" in enabled_entities_for_entry(entry)
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe directly to this account's coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_new_email_listener(self._handle_new_email)
        )

    @callback
    def _handle_new_email(self, event_data: dict[str, Any]) -> None:
        """Publish one already bounded, body-free event payload."""
        self._trigger_event(EVENT_TYPE_NEW_EMAIL, event_data)
        self.async_write_ha_state()


class EmailWatchEventEntity(EventEntity):
    """Expose matches and health for one persistent user-managed Email watch."""

    _attr_event_types = [EVENT_TYPE_NEW_MATCHING_EMAIL]
    _attr_has_entity_name = True
    _attr_icon = "mdi:email-alert-outline"

    def __init__(
        self,
        coordinator: EmailDataUpdateCoordinator,
        entry: ConfigEntry,
        watch: dict[str, Any],
    ) -> None:
        self._coordinator = coordinator
        self._watch = watch
        self._watch_id = str(watch["id"])
        self._attr_name = str(watch["name"])
        self._attr_unique_id = f"{entry.entry_id}_watch_{self._watch_id}"
        self._attr_device_info = gmail_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe only to this account and persistent watch ID."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_watch_listener(
                self._watch_id, self._handle_match
            )
        )
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose watch state and privacy-safe rule health."""
        health = self._coordinator.rule_health(self._watch_id)
        return {
            "folder": self._watch.get(CONF_FOLDER, DEFAULT_FOLDER),
            "enabled": bool(self._watch.get("enabled", True)),
            "catch_up": bool(self._watch.get(CONF_CATCH_UP, False)),
            "rule_status": health.status,
            "last_successful_check": health.last_successful_check,
            "last_error_at": health.last_error_at,
            "last_error_type": health.last_error_type,
            "last_error": health.last_error,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh health attributes even when no email match fires."""
        self.async_write_ha_state()

    @callback
    def _handle_match(self, event_data: dict[str, Any]) -> None:
        """Publish one already bounded, body-free matching event."""
        self._trigger_event(EVENT_TYPE_NEW_MATCHING_EMAIL, event_data)
        self.async_write_ha_state()
