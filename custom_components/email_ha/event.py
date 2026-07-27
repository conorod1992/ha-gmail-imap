"""Discoverable new-email event entity for Email HA."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_EMAIL, DOMAIN, EVENT_NEW_EMAIL, EVENT_TYPE_NEW_EMAIL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the new-email event entity for one Gmail account."""
    async_add_entities([NewEmailEventEntity(entry)])


class NewEmailEventEntity(EventEntity):
    """Expose new mail as a first-class Home Assistant event entity."""

    _attr_event_types = [EVENT_TYPE_NEW_EMAIL]
    _attr_has_entity_name = True
    _attr_icon = "mdi:email-fast-outline"
    _attr_translation_key = "new_email"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_new_email_event"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Gmail – {entry.data[CONF_EMAIL].split('@')[0]}",
            manufacturer="Google",
            model="Gmail IMAP (OAuth2)",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe after the entity is ready to write state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_NEW_EMAIL, self._handle_new_email)
        )

    @callback
    def _handle_new_email(self, event: Event) -> None:
        """Forward this account's legacy bus event through the event entity."""
        if event.data.get("config_entry_id") != self._entry.entry_id:
            return
        self._trigger_event(EVENT_TYPE_NEW_EMAIL, dict(event.data))
        self.async_write_ha_state()
