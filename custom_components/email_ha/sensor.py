"""Gmail-facing and custom count sensors for Email HA."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DATE,
    ATTR_FOLDER,
    ATTR_SENDER_ADDRESS,
    ATTR_SENDER_NAME,
    ATTR_UID,
    CONF_CUSTOM_SENSORS,
    CONF_FOLDER,
    DEFAULT_FOLDER,
    DOMAIN,
    UNAVAILABLE_AFTER_SECONDS,
)
from .coordinator import EmailData, EmailDataUpdateCoordinator
from .entity import gmail_device_info
from .gmail import (
    GMAIL_SENSOR_DEFINITIONS,
    GmailEntityDefinition,
    enabled_entities_for_entry,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all fixed entities plus configured custom sensors."""
    coordinator: EmailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        GmailSensor(coordinator, entry, definition)
        for definition in GMAIL_SENSOR_DEFINITIONS
    ]
    entities.extend(
        CustomEmailCountSensor(coordinator, entry, sensor)
        for sensor in entry.options.get(CONF_CUSTOM_SENSORS, [])
    )
    entities.extend([ConnectionStatusSensor(coordinator, entry), LastSuccessfulUpdateSensor(coordinator, entry)])
    async_add_entities(entities)


class _BaseEmailSensor(CoordinatorEntity[EmailDataUpdateCoordinator], SensorEntity):
    """Base class shared by fixed and custom sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EmailDataUpdateCoordinator,
        entry: ConfigEntry,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = gmail_device_info(entry)

    @property
    def available(self) -> bool:
        """Remain available only while the shared account data is fresh."""
        if self.coordinator.data is None or self.coordinator.last_success_time is None:
            return False
        elapsed = (
            datetime.now(timezone.utc) - self.coordinator.last_success_time
        ).total_seconds()
        if elapsed > UNAVAILABLE_AFTER_SECONDS:
            _LOGGER.warning(
                "%s unavailable: no successful update for %.0f seconds",
                self.entity_id,
                elapsed,
            )
            return False
        return True

    @property
    def _email_data(self) -> EmailData | None:
        return self.coordinator.data


class GmailSensor(_BaseEmailSensor):
    """One fixed, translated Gmail concept."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: EmailDataUpdateCoordinator,
        entry: ConfigEntry,
        definition: GmailEntityDefinition,
    ) -> None:
        self._definition = definition
        self._attr_translation_key = definition.key
        self._attr_icon = definition.icon
        self._attr_entity_registry_enabled_default = (
            definition.key in enabled_entities_for_entry(entry)
        )
        if definition.source == "latest_email":
            self._attr_state_class = None
            self._attr_native_unit_of_measurement = None
            self._attr_suggested_display_precision = None
        elif definition.source == "folder_count":
            self._attr_native_unit_of_measurement = "folders"
        super().__init__(coordinator, entry, definition.key)

    @property
    def native_value(self) -> int | str | None:
        """Return state from the source named by the canonical definition."""
        data = self._email_data
        if data is None:
            return None
        if self._definition.source == "inbox_unread":
            return data.inbox_unread
        if self._definition.source == "inbox_total":
            return data.inbox_total
        if self._definition.source == "folder_count":
            return len(data.folders)
        if self._definition.source == "latest_email":
            if data.latest_email:
                return data.latest_email.get("subject") or "(no subject)"
            return None
        result = data.gmail_counts.get(self._definition.key)
        return result.count if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful metadata without filter values or message bodies."""
        data = self._email_data
        if self._definition.source == "folder_count":
            return {"folders": data.folders if data else []}
        if self._definition.source == "latest_email":
            if not data or not data.latest_email:
                return {}
            message = data.latest_email
            sender = message.get("sender") or {}
            return {
                ATTR_SENDER_NAME: sender.get("name", ""),
                ATTR_SENDER_ADDRESS: sender.get("address", ""),
                ATTR_DATE: message.get("date"),
                ATTR_UID: message.get("uid"),
                ATTR_FOLDER: self.coordinator.folder,
            }
        return {ATTR_FOLDER: DEFAULT_FOLDER}


class CustomEmailCountSensor(_BaseEmailSensor):
    """Count emails matching one user-managed server-side filter."""

    _attr_icon = "mdi:email-search-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: EmailDataUpdateCoordinator,
        entry: ConfigEntry,
        sensor: dict[str, Any],
    ) -> None:
        self._sensor = sensor
        self._sensor_id = str(sensor["id"])
        self._attr_name = str(sensor["name"])
        super().__init__(coordinator, entry, f"custom_{self._sensor_id}")

    @property
    def native_value(self) -> int | None:
        data = self._email_data
        result = data.custom_counts.get(self._sensor_id) if data else None
        return result.count if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._email_data
        result = data.custom_counts.get(self._sensor_id) if data else None
        filters = self._sensor.get("filters", {})
        return {
            ATTR_FOLDER: self._sensor.get(CONF_FOLDER, DEFAULT_FOLDER),
            "filter_types": sorted(filters),
            "newest_matching_uid": result.newest_uid if result else None,
            "newest_matching_subject": result.newest_subject if result else None,
            "newest_matching_sender_name": (
                result.newest_sender_name if result else None
            ),
            "newest_matching_sender_address": (
                result.newest_sender_address if result else None
            ),
            "newest_matching_date": result.newest_date if result else None,
            "last_new_match": result.last_new_match if result else None,
        }


class ConnectionStatusSensor(_BaseEmailSensor):
    """A disabled-by-default, actionable account health indicator."""

    _attr_translation_key = "connection_status"
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:email-check-outline"

    def __init__(self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "connection_status")

    @property
    def native_value(self) -> str:
        return "Connected" if self.coordinator.last_update_success else "Unavailable"


class LastSuccessfulUpdateSensor(_BaseEmailSensor):
    """A disabled-by-default timestamp for diagnosing stale connections."""

    _attr_translation_key = "last_successful_update"
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_successful_update")

    @property
    def native_value(self) -> str | None:
        timestamp = self.coordinator.last_success_time
        return timestamp.isoformat() if timestamp else None
