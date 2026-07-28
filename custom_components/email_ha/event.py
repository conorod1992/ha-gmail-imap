"""Discoverable New email EventEntity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_TYPE_NEW_EMAIL
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
    async_add_entities([NewEmailEventEntity(coordinator, entry)])


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
