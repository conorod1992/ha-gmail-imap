"""Sensor platform for Email IMAP."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DATE,
    ATTR_FOLDER,
    ATTR_SENDER_EMAIL,
    ATTR_SENDER_NAME,
    ATTR_SUBJECT,
    ATTR_UID,
    CONF_EMAIL,
    CONF_FOLDER,
    CONF_SEARCH_SENSORS,
    DOMAIN,
    UNAVAILABLE_AFTER_SECONDS,
)
from .coordinator import EmailData, EmailDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Email IMAP sensors for a config entry."""
    coordinator: EmailDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        UnreadCountSensor(coordinator, entry),
        TotalCountSensor(coordinator, entry),
        FoldersSensor(coordinator, entry),
        LastEmailSensor(coordinator, entry),
    ]
    entities.extend(
        SearchCountSensor(coordinator, entry, monitor)
        for monitor in entry.options.get(CONF_SEARCH_SENSORS, [])
    )
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Gmail – {entry.data[CONF_EMAIL].split('@')[0]}",
        manufacturer="Google",
        model="Gmail IMAP (OAuth2)",
        entry_type=DeviceEntryType.SERVICE,
    )


def _entry_folder(entry: ConfigEntry) -> str:
    """Return the effective configured folder, including options."""
    return entry.options.get(CONF_FOLDER, entry.data.get(CONF_FOLDER, "INBOX"))


class _BaseEmailSensor(CoordinatorEntity[EmailDataUpdateCoordinator], SensorEntity):
    """Base class for Email IMAP sensors."""

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
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        if self.coordinator.data is None:
            _LOGGER.debug("%s unavailable: no data yet", self.entity_id)
            return False
        last_success = self.coordinator.last_success_time
        if last_success is None:
            _LOGGER.debug("%s unavailable: last_success_time not set", self.entity_id)
            return False
        elapsed = (datetime.now(timezone.utc) - last_success).total_seconds()
        if elapsed > UNAVAILABLE_AFTER_SECONDS:
            _LOGGER.warning(
                "%s unavailable: no successful update for %.0fs (threshold %ds); "
                "coordinator last_update_success=%s",
                self.entity_id,
                elapsed,
                UNAVAILABLE_AFTER_SECONDS,
                self.coordinator.last_update_success,
            )
            return False
        return True

    @property
    def _email_data(self) -> EmailData | None:
        return self.coordinator.data


class UnreadCountSensor(_BaseEmailSensor):
    """Number of unread messages in the monitored folder."""

    _attr_name = "Unread count"
    _attr_icon = "mdi:email-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "unread_count")

    @property
    def native_value(self) -> int | None:
        data = self._email_data
        return data.unread_count if data else None

    @property
    def extra_state_attributes(self) -> dict:
        return {ATTR_FOLDER: _entry_folder(self._entry)}


class TotalCountSensor(_BaseEmailSensor):
    """Total number of messages in the monitored folder."""

    _attr_name = "Total count"
    _attr_icon = "mdi:email-multiple-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "total_count")

    @property
    def native_value(self) -> int | None:
        data = self._email_data
        return data.total_count if data else None

    @property
    def extra_state_attributes(self) -> dict:
        return {ATTR_FOLDER: _entry_folder(self._entry)}


class FoldersSensor(_BaseEmailSensor):
    """Number of mailbox folders on the account, with folder list as attribute."""

    _attr_name = "Folders"
    _attr_icon = "mdi:folder-multiple-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "folders"
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "folders")

    @property
    def native_value(self) -> int | None:
        data = self._email_data
        return len(data.folders) if data else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._email_data
        return {"folders": data.folders if data else []}


class LastEmailSensor(_BaseEmailSensor):
    """Subject line of the most-recently received email."""

    _attr_name = "Last email"
    _attr_icon = "mdi:email"

    def __init__(
        self, coordinator: EmailDataUpdateCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "last_email")

    @property
    def native_value(self) -> str | None:
        data = self._email_data
        if data and data.latest_email:
            return data.latest_email.get(ATTR_SUBJECT) or "(no subject)"
        return None

    @property
    def extra_state_attributes(self) -> dict:
        data = self._email_data
        if not data or not data.latest_email:
            return {}
        email = data.latest_email
        recent = [
            {
                ATTR_SUBJECT: e.get(ATTR_SUBJECT),
                ATTR_SENDER_NAME: e.get(ATTR_SENDER_NAME),
                ATTR_SENDER_EMAIL: e.get(ATTR_SENDER_EMAIL),
            }
            for e in data.emails[:3]
        ]
        return {
            ATTR_SENDER_NAME: email.get(ATTR_SENDER_NAME),
            ATTR_SENDER_EMAIL: email.get(ATTR_SENDER_EMAIL),
            ATTR_DATE: email.get(ATTR_DATE),
            ATTR_UID: email.get(ATTR_UID),
            ATTR_FOLDER: _entry_folder(self._entry),
            "recent_emails": recent,
        }


class SearchCountSensor(_BaseEmailSensor):
    """Count of messages matching one optional structured search."""

    _attr_icon = "mdi:email-search-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "messages"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: EmailDataUpdateCoordinator,
        entry: ConfigEntry,
        monitor: dict,
    ) -> None:
        self._monitor = monitor
        self._monitor_id = str(monitor["id"])
        self._attr_name = str(monitor["name"])
        super().__init__(coordinator, entry, f"search_{self._monitor_id}")

    @property
    def native_value(self) -> int | None:
        data = self._email_data
        if not data or not (result := data.search_counts.get(self._monitor_id)):
            return None
        return result.count

    @property
    def extra_state_attributes(self) -> dict:
        data = self._email_data
        result = data.search_counts.get(self._monitor_id) if data else None
        filters = self._monitor.get("filters", {})
        return {
            ATTR_FOLDER: self._monitor.get(CONF_FOLDER, "INBOX"),
            "filter_types": sorted(filters),
            "newest_matching_uid": result.newest_uid if result else None,
        }
