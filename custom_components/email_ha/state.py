"""Small durable state for reliable Email HA watches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION = 1
_SAVE_DELAY = 5
_MAX_FOLDERS = 100
_MAX_MATCH_ENTRIES = 100


class EmailStateStore:
    """Persist only UIDs and timestamps needed for reliable restart behavior."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.state",
            private=True,
            atomic_writes=True,
        )
        self.folder_uid_state: dict[str, tuple[int | None, int]] = {}
        self.custom_last_new_match: dict[str, str] = {}
        self.watch_last_new_match: dict[str, str] = {}

    async def async_load(self) -> None:
        """Load validated lightweight state, ignoring malformed entries."""
        raw = await self._store.async_load()
        if not isinstance(raw, Mapping):
            return

        folders = raw.get("folders")
        if isinstance(folders, Mapping):
            for folder, value in list(folders.items())[:_MAX_FOLDERS]:
                if not isinstance(folder, str) or not isinstance(value, Mapping):
                    continue
                uid_validity = value.get("uidvalidity")
                last_seen_uid = value.get("last_seen_uid")
                if uid_validity is not None and not isinstance(uid_validity, int):
                    continue
                if not isinstance(last_seen_uid, int) or last_seen_uid < 0:
                    continue
                self.folder_uid_state[folder] = (uid_validity, last_seen_uid)

        self.custom_last_new_match = self._load_matches(
            raw.get("custom_last_new_match")
        )
        self.watch_last_new_match = self._load_matches(raw.get("watch_last_new_match"))

    @staticmethod
    def _load_matches(value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            return {}
        result: dict[str, str] = {}
        for key, timestamp in list(value.items())[:_MAX_MATCH_ENTRIES]:
            if isinstance(key, str) and isinstance(timestamp, str):
                result[key] = timestamp
        return result

    @callback
    def async_schedule_save(self) -> None:
        """Coalesce frequent IDLE refreshes into small durable writes."""
        self._store.async_delay_save(self._data_to_save, _SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "folders": {
                folder: {
                    "uidvalidity": uid_validity,
                    "last_seen_uid": last_seen_uid,
                }
                for folder, (uid_validity, last_seen_uid) in self.folder_uid_state.items()
            },
            "custom_last_new_match": dict(self.custom_last_new_match),
            "watch_last_new_match": dict(self.watch_last_new_match),
        }
