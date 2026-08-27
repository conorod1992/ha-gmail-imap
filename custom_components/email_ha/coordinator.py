"""Shared Gmail IMAP coordinator with IDLE and bounded event tracking."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

import aioimaplib

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CATCH_UP,
    DEFAULT_FOLDER,
    DOMAIN,
    IDLE_FALLBACK_REFRESH_INTERVAL,
    IDLE_PUSH_WAIT_TIMEOUT,
    IDLE_RECONNECT_DELAYS,
    LATEST_EMAIL_FETCH_COUNT,
    MAX_CATCH_UP_EVENTS,
    MAX_NEW_EMAIL_EVENTS,
)
from .gmail import GMAIL_SEARCH_DEFINITIONS
from .imap_client import ImapAuthError, ImapClient, ImapClientError, ImapFolderError
from .repairs import clear_folder_unavailable, report_folder_unavailable
from .search import build_structured_search_tokens
from .state import EmailStateStore

_LOGGER = logging.getLogger(__name__)

_FOLDER_REFRESH_INTERVAL = 86400
_CATCH_UP_MARKER = "_email_ha_caught_up"


@dataclass(slots=True)
class SearchCountData:
    """Bounded state for one server-side count sensor."""

    count: int | None = None
    newest_uid: str | None = None
    newest_subject: str | None = None
    newest_sender_name: str | None = None
    newest_sender_address: str | None = None
    newest_date: str | None = None
    last_new_match: str | None = None


@dataclass(slots=True)
class EmailData:
    """Current read-only mailbox state for one account."""

    emails: list[dict[str, Any]] = field(default_factory=list)
    inbox_unread: int = 0
    inbox_total: int = 0
    folders: list[str] = field(default_factory=list)
    gmail_counts: dict[str, SearchCountData] = field(default_factory=dict)
    custom_counts: dict[str, SearchCountData] = field(default_factory=dict)
    new_emails: list[dict[str, Any]] = field(default_factory=list)
    watch_matches: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def latest_email(self) -> dict[str, Any] | None:
        """Return the most recently received message metadata."""
        return self.emails[0] if self.emails else None


class EmailDataUpdateCoordinator(DataUpdateCoordinator[EmailData]):
    """Manage one shared IMAP connection per refresh and an IDLE task."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        oauth_session: OAuth2Session,
        email_address: str,
        imap_host: str,
        imap_port: int,
        folder: str,
        enabled_gmail_entities: set[str] | None = None,
        custom_sensors: list[dict[str, Any]] | None = None,
        email_watches: list[dict[str, Any]] | None = None,
    ) -> None:
        self.oauth_session = oauth_session
        self._email = email_address
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._folder = folder
        self.enabled_gmail_entities = enabled_gmail_entities or set()
        self.custom_sensors = custom_sensors or []
        self.email_watches = email_watches or []
        self.last_success_time: datetime | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._cached_folders: list[str] = []
        self._folders_fetched_at = 0.0
        self._event_baseline_ready = False
        self._uid_validity: int | None = None
        self._last_seen_uid = 0
        self._update_lock = asyncio.Lock()
        self._new_email_listeners: set[Callable[[dict[str, Any]], None]] = set()
        self._watch_listeners: dict[str, set[Callable[[dict[str, Any]], None]]] = {}
        self._folder_uid_state: dict[str, tuple[int | None, int]] = {}
        self._restored_folders: set[str] = set()
        self._custom_last_new_match: dict[str, str] = {}
        self._watch_last_new_match: dict[str, str] = {}
        self._state_store = EmailStateStore(hass, config_entry.entry_id)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}:{email_address}",
            update_interval=timedelta(seconds=IDLE_FALLBACK_REFRESH_INTERVAL),
        )

    async def async_load_state(self) -> None:
        """Load restart-safe UID baselines and lightweight match timestamps."""
        try:
            await self._state_store.async_load()
        except Exception as err:  # noqa: BLE001 - durable state must never block setup
            _LOGGER.warning(
                "Unable to load persisted Email HA state for %s: %s",
                self._email,
                type(err).__name__,
            )
            return
        self._folder_uid_state = dict(self._state_store.folder_uid_state)
        self._restored_folders = set(self._folder_uid_state)
        custom_ids = {str(item.get("id", "")) for item in self.custom_sensors}
        watch_ids = {str(item.get("id", "")) for item in self.email_watches}
        self._custom_last_new_match = {
            key: value
            for key, value in self._state_store.custom_last_new_match.items()
            if key in custom_ids
        }
        self._watch_last_new_match = {
            key: value
            for key, value in self._state_store.watch_last_new_match.items()
            if key in watch_ids
        }

    @callback
    def _schedule_state_save(self) -> None:
        """Persist only UID baselines and timestamps, never message content."""
        state_store = getattr(self, "_state_store", None)
        if state_store is None:
            return
        state_store.folder_uid_state = dict(self._folder_uid_state)
        state_store.custom_last_new_match = dict(self._custom_last_new_match)
        state_store.watch_last_new_match = dict(
            getattr(self, "_watch_last_new_match", {})
        )
        state_store.async_schedule_save()

    @property
    def folder(self) -> str:
        """Return the folder used by Latest email and New email."""
        return self._folder

    @property
    def idle_running(self) -> bool:
        """Return whether the account's IDLE task is currently alive."""
        return self._idle_task is not None and not self._idle_task.done()

    @property
    def cached_folder_count(self) -> int:
        """Return the number of folders discovered during the last refresh."""
        return len(self._cached_folders)

    @property
    def event_baseline_ready(self) -> bool:
        """Return whether new-email UID baselines are established."""
        return self._event_baseline_ready

    @property
    def persisted_folder_count(self) -> int:
        """Return the number of durable folder UID baselines."""
        return len(self._folder_uid_state)

    @callback
    def async_add_new_email_listener(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register an EventEntity listener without exposing a bus event."""
        self._new_email_listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._new_email_listeners.discard(listener)

        return remove_listener

    @callback
    def async_add_watch_listener(
        self, watch_id: str, listener: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a listener for one persistent Email watch ID."""
        listeners = self._watch_listeners.setdefault(watch_id, set())
        listeners.add(listener)

        @callback
        def remove_listener() -> None:
            listeners.discard(listener)
            if not listeners:
                self._watch_listeners.pop(watch_id, None)

        return remove_listener

    async def _async_ensure_fresh_token(self) -> str:
        """Proactively refresh OAuth and return an access token."""
        try:
            token: dict[str, Any] = self.oauth_session.token
            expires_in = token.get("expires_at", 0) - time.time()
            if expires_in < 600 and self.config_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        "token": {**token, "expires_at": time.time() - 1},
                    },
                )
            await self.oauth_session.async_ensure_token_valid()
        except Exception as err:
            _LOGGER.warning(
                "Token refresh failed for %s: %s", self._email, type(err).__name__
            )
            raise ConfigEntryAuthFailed(
                f"Token refresh failed for {self._email}: {type(err).__name__}"
            ) from err
        raw = self.oauth_session.token["access_token"]
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def async_preview_filter(
        self, folder: str, filters: dict[str, Any], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Run a bounded, body-free draft search without touching coordinator state."""
        tokens = build_structured_search_tokens(filters)
        access_token = await self._async_ensure_fresh_token()
        async with ImapClient(self._imap_host, self._imap_port) as client:
            await client.connect(self._email, access_token)
            return await client.search_emails_tokens(
                folder, tokens, limit, include_body=False
            )

    async def _async_detect_new_emails(
        self, client: ImapClient, status: dict[str, int]
    ) -> list[dict[str, Any]]:
        """Detect generic new-email events without replaying history after restart."""
        uid_validity = status.get("uidvalidity")
        current_highest_uid = max(0, status.get("uidnext", 1) - 1)

        if not self._event_baseline_ready or uid_validity != self._uid_validity:
            self._event_baseline_ready = True
            self._uid_validity = uid_validity
            self._last_seen_uid = current_highest_uid
            return []

        if current_highest_uid <= self._last_seen_uid:
            return []

        if "new_email" not in self.enabled_gmail_entities:
            self._last_seen_uid = current_highest_uid
            return []

        messages, match_count = await client.get_new_emails(
            self._folder, self._last_seen_uid, MAX_NEW_EMAIL_EVENTS
        )
        self._last_seen_uid = current_highest_uid
        if match_count > MAX_NEW_EMAIL_EVENTS:
            _LOGGER.warning(
                "Received %d messages between updates for %s; emitting the newest %d",
                match_count,
                self._email,
                MAX_NEW_EMAIL_EVENTS,
            )
        return messages

    async def _async_detect_folder_new_emails(
        self,
        client: ImapClient,
        folder: str,
        status: dict[str, int],
        *,
        fetch_messages: bool,
        allow_catch_up: bool = False,
    ) -> list[dict[str, Any]]:
        """Detect filtered arrivals, optionally resuming a persisted restart baseline."""
        uid_validity = status.get("uidvalidity")
        highest_uid = max(0, status.get("uidnext", 1) - 1)
        previous = self._folder_uid_state.get(folder)
        restored_folders = getattr(self, "_restored_folders", set())
        restored = folder in restored_folders
        restored_folders.discard(folder)

        if previous is None or previous[0] != uid_validity:
            self._folder_uid_state[folder] = (uid_validity, highest_uid)
            return []

        last_seen = previous[1]
        self._folder_uid_state[folder] = (uid_validity, highest_uid)
        if highest_uid <= last_seen or not fetch_messages:
            return []

        if restored and not allow_catch_up:
            return []

        limit = MAX_CATCH_UP_EVENTS if restored else MAX_NEW_EMAIL_EVENTS
        messages, match_count = await client.get_new_emails(folder, last_seen, limit)
        if match_count > limit:
            _LOGGER.warning(
                "Received %d messages since the previous %s baseline for %s; checking the newest %d",
                match_count,
                "persisted" if restored else "live",
                self._email,
                limit,
            )
        return messages

    @staticmethod
    def _count_data(
        count: int,
        newest_uid: str | None,
        message: dict[str, Any] | None,
        last: str | None,
    ) -> SearchCountData:
        """Build privacy-conscious newest-message state."""
        sender = (message or {}).get("sender") or {}
        return SearchCountData(
            count=count,
            newest_uid=newest_uid,
            newest_subject=(message or {}).get("subject"),
            newest_sender_name=sender.get("name"),
            newest_sender_address=sender.get("address"),
            newest_date=(message or {}).get("date"),
            last_new_match=last,
        )

    async def _async_match_new_messages(
        self,
        client: ImapClient,
        arrivals: dict[str, list[dict[str, Any]]],
        catch_up_only_folders: set[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Match bounded arrivals, restricting restart batches to opted-in watches."""
        catch_up_only_folders = catch_up_only_folders or set()
        watch_matches: list[tuple[str, dict[str, Any]]] = []
        observed_at = datetime.now(timezone.utc).isoformat()
        if not hasattr(self, "_watch_last_new_match"):
            self._watch_last_new_match = {}
        for definition, is_watch in (
            *((sensor, False) for sensor in self.custom_sensors),
            *((watch, True) for watch in self.email_watches),
        ):
            if is_watch and not definition.get("enabled", True):
                continue
            definition_id = str(definition.get("id", ""))
            folder = str(definition.get("folder", DEFAULT_FOLDER))
            caught_up = folder in catch_up_only_folders
            if caught_up and (
                not is_watch or not definition.get(CONF_CATCH_UP, False)
            ):
                continue
            if not definition_id or not arrivals.get(folder):
                continue
            try:
                tokens = build_structured_search_tokens(definition.get("filters", {}))
                matching_uids = await client.matching_uids(
                    folder,
                    [str(message.get("uid", "")) for message in arrivals[folder]],
                    tokens,
                )
                for message in arrivals[folder]:
                    if str(message.get("uid", "")) not in matching_uids:
                        continue
                    if is_watch:
                        payload_message = dict(message)
                        if caught_up:
                            payload_message[_CATCH_UP_MARKER] = True
                        watch_matches.append((definition_id, payload_message))
                        self._watch_last_new_match[definition_id] = observed_at
                    else:
                        self._custom_last_new_match[definition_id] = observed_at
            except (ImapClientError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to match filtered definition %s for %s: %s",
                    definition_id,
                    self._email,
                    type(err).__name__,
                )
        return watch_matches

    async def _async_folder_status(
        self, client: ImapClient, folder: str, *, required: bool
    ) -> dict[str, int] | None:
        """Read folder status and maintain an actionable repair for missing folders."""
        entry = getattr(self, "config_entry", None)
        hass = getattr(self, "hass", None)
        try:
            status = await client.get_folder_status(folder)
        except ImapFolderError:
            if entry is not None and hass is not None:
                report_folder_unavailable(hass, entry, folder)
            if required:
                raise
            _LOGGER.warning(
                "Configured folder %s is unavailable for %s", folder, self._email
            )
            return None
        except (ImapClientError, ValueError) as err:
            if required:
                raise
            _LOGGER.warning(
                "Unable to query tracked folder %s for %s: %s",
                folder,
                self._email,
                type(err).__name__,
            )
            return None
        if entry is not None and hass is not None:
            clear_folder_unavailable(hass, entry, folder)
        return status

    async def _async_fetch_data(self, client: ImapClient) -> EmailData:
        """Fetch all enabled state through one connected client."""
        monitored_status = await self._async_folder_status(
            client, self._folder, required=True
        )
        assert monitored_status is not None
        if self._folder.upper() == DEFAULT_FOLDER:
            inbox_status = monitored_status
        else:
            inbox_status = await self._async_folder_status(
                client, DEFAULT_FOLDER, required=True
            )
            assert inbox_status is not None

        if time.monotonic() - self._folders_fetched_at > _FOLDER_REFRESH_INTERVAL:
            self._cached_folders = await client.list_folders()
            self._folders_fetched_at = time.monotonic()

        emails = (
            await client.search_emails(self._folder, "ALL", LATEST_EMAIL_FETCH_COUNT)
            if "latest_email" in self.enabled_gmail_entities
            else []
        )
        gmail_counts: dict[str, SearchCountData] = {}
        for definition in GMAIL_SEARCH_DEFINITIONS:
            if definition.key not in self.enabled_gmail_entities:
                continue
            tokens = build_structured_search_tokens(definition.filters)
            count, newest_uid = await client.count_emails(DEFAULT_FOLDER, tokens)
            gmail_counts[definition.key] = SearchCountData(count, newest_uid)

        tracked_definitions = [*self.custom_sensors, *self.email_watches]
        tracked_folders = {
            str(item.get("folder", DEFAULT_FOLDER)) for item in tracked_definitions
        }
        active_filtered_folders = {
            str(item.get("folder", DEFAULT_FOLDER))
            for item in (
                *self.custom_sensors,
                *(watch for watch in self.email_watches if watch.get("enabled", True)),
            )
        }
        catch_up_folders = {
            str(watch.get("folder", DEFAULT_FOLDER))
            for watch in self.email_watches
            if watch.get("enabled", True) and watch.get(CONF_CATCH_UP, False)
        }

        folder_statuses: dict[str, dict[str, int]] = {self._folder: monitored_status}
        for folder in tracked_folders - {self._folder}:
            if status := await self._async_folder_status(client, folder, required=False):
                folder_statuses[folder] = status

        # Generic New email deliberately retains its no-replay startup semantics.
        new_emails = await self._async_detect_new_emails(client, monitored_status)

        arrivals: dict[str, list[dict[str, Any]]] = {}
        catch_up_only_folders: set[str] = set()
        for folder, status in folder_statuses.items():
            was_restored = folder in getattr(self, "_restored_folders", set())
            allow_catch_up = folder in catch_up_folders
            messages = await self._async_detect_folder_new_emails(
                client,
                folder,
                status,
                fetch_messages=folder in active_filtered_folders,
                allow_catch_up=allow_catch_up,
            )
            arrivals[folder] = messages
            if was_restored and allow_catch_up and messages:
                catch_up_only_folders.add(folder)

        watch_matches = await self._async_match_new_messages(
            client, arrivals, catch_up_only_folders
        )

        custom_counts: dict[str, SearchCountData] = {}
        newest_metadata: dict[tuple[str, str], dict[str, Any] | None] = {}
        for sensor in self.custom_sensors:
            sensor_id = str(sensor.get("id", ""))
            if not sensor_id:
                continue
            try:
                tokens = build_structured_search_tokens(sensor.get("filters", {}))
                folder = str(sensor.get("folder", DEFAULT_FOLDER))
                count, newest_uid = await client.count_emails(folder, tokens)
                newest = None
                if newest_uid:
                    cache_key = (folder, newest_uid)
                    if cache_key not in newest_metadata:
                        newest_metadata[cache_key] = await client.get_email_metadata(
                            folder, newest_uid
                        )
                    newest = newest_metadata[cache_key]
            except (ImapClientError, ValueError) as err:
                _LOGGER.warning(
                    "Unable to update custom sensor %s for %s: %s",
                    sensor_id,
                    self._email,
                    type(err).__name__,
                )
                custom_counts[sensor_id] = SearchCountData()
            else:
                custom_counts[sensor_id] = self._count_data(
                    count,
                    newest_uid,
                    newest,
                    self._custom_last_new_match.get(sensor_id),
                )

        self.last_success_time = datetime.now(timezone.utc)
        self._schedule_state_save()
        return EmailData(
            emails=emails,
            inbox_unread=inbox_status.get("unseen", 0),
            inbox_total=inbox_status.get("messages", 0),
            folders=self._cached_folders,
            gmail_counts=gmail_counts,
            custom_counts=custom_counts,
            new_emails=new_emails,
            watch_matches=watch_matches,
        )

    @callback
    def _notify_new_emails(self, messages: list[dict[str, Any]]) -> None:
        """Deliver privacy-conscious payloads oldest UID to newest UID."""
        for message in messages:
            sender = message.get("sender") or {}
            payload = {
                "account": self._email,
                "folder": self._folder,
                "uid": message.get("uid"),
                "message_id": message.get("message_id"),
                "subject": message.get("subject"),
                "sender_name": sender.get("name", ""),
                "sender_address": sender.get("address", ""),
                "date": message.get("date"),
            }
            for listener in tuple(self._new_email_listeners):
                listener(payload)

    @callback
    def _notify_watch_matches(
        self, matches: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """Deliver bounded, body-free matches to their watch EventEntities."""
        definitions = {str(watch.get("id", "")): watch for watch in self.email_watches}
        last_matches = getattr(self, "_watch_last_new_match", {})
        for watch_id, message in matches:
            watch = definitions.get(watch_id)
            if watch is None or not watch.get("enabled", True):
                continue
            sender = message.get("sender") or {}
            payload = {
                "account": self._email,
                "folder": watch.get("folder", DEFAULT_FOLDER),
                "uid": message.get("uid"),
                "message_id": message.get("message_id"),
                "subject": message.get("subject"),
                "sender_name": sender.get("name", ""),
                "sender_address": sender.get("address", ""),
                "date": message.get("date"),
                "watch_id": watch_id,
                "watch_name": watch.get("name", "Email watch"),
                "caught_up": bool(message.get(_CATCH_UP_MARKER, False)),
                "last_new_match": last_matches.get(watch_id),
            }
            for listener in tuple(self._watch_listeners.get(watch_id, ())):
                listener(payload)

    async def _async_update_data(self) -> EmailData:
        """Run the internal fallback refresh used when IDLE misses an update."""
        async with self._update_lock:
            access_token = await self._async_ensure_fresh_token()
            try:
                async with ImapClient(self._imap_host, self._imap_port) as client:
                    await client.connect(self._email, access_token)
                    data = await self._async_fetch_data(client)
            except ImapAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except Exception as err:
                raise UpdateFailed(
                    f"IMAP error for {self._email}: {type(err).__name__}"
                ) from err

        self._notify_new_emails(data.new_emails)
        self._notify_watch_matches(data.watch_matches)
        return data

    def start_idle(self) -> None:
        """Start the IDLE background task."""
        if self._idle_task is not None:
            return
        self._idle_task = self.hass.async_create_background_task(
            self._async_idle_loop(), name=f"{DOMAIN}:idle:{self._email}"
        )

    async def stop_idle(self) -> None:
        """Cancel IDLE and wait for a clean shutdown."""
        if self._idle_task is None:
            return
        self._idle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._idle_task
        self._idle_task = None

    async def _async_idle_loop(self) -> None:
        """Reconnect IDLE with bounded exponential backoff."""
        reconnect_attempt = 0
        while True:
            reconnect_attempt = await self._async_idle_attempt(reconnect_attempt)

    async def _async_idle_attempt(self, reconnect_attempt: int) -> int:
        """Run one IDLE session attempt."""
        try:
            await self._async_run_idle_session()
        except ConfigEntryAuthFailed:
            raise
        except asyncio.CancelledError:
            raise
        except (OSError, aioimaplib.AioImapException, ImapClientError) as err:
            delay = IDLE_RECONNECT_DELAYS[
                min(reconnect_attempt, len(IDLE_RECONNECT_DELAYS) - 1)
            ]
            _LOGGER.warning(
                "IDLE error for %s: %s; reconnecting in %ds",
                self._email,
                type(err).__name__,
                delay,
            )
            await asyncio.sleep(delay)
            return reconnect_attempt + 1
        return 0

    async def _async_run_idle_session(self) -> None:
        """Connect once, refresh after pushes, and renew before token expiry."""
        access_token = await self._async_ensure_fresh_token()
        token_expires_at: float = self.oauth_session.token.get("expires_at", 0)
        client = ImapClient(self._imap_host, self._imap_port)
        try:
            await client.connect(self._email, access_token)
            while True:
                if time.time() > token_expires_at - 600:
                    return
                async with self._update_lock:
                    data = await self._async_fetch_data(client)
                    self.async_set_updated_data(data)
                    self._notify_new_emails(data.new_emails)
                    self._notify_watch_matches(data.watch_matches)

                idle_timeout = min(
                    float(IDLE_PUSH_WAIT_TIMEOUT),
                    max(60.0, token_expires_at - time.time() - 60),
                )
                await client.select_folder_read_only(self._folder)
                await client.idle_wait(idle_timeout)
        except ImapAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        finally:
            await client.disconnect()


def coordinator_from_entry(
    hass: HomeAssistant, entry_id: str
) -> EmailDataUpdateCoordinator | None:
    """Return the coordinator for a config entry."""
    return hass.data.get(DOMAIN, {}).get(entry_id)
