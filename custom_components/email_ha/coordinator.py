"""DataUpdateCoordinator for Email IMAP."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any

import aioimaplib

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    EVENT_NEW_EMAIL,
    IDLE_FALLBACK_POLL_INTERVAL,
    IDLE_PUSH_WAIT_TIMEOUT,
    IDLE_RECONNECT_DELAYS,
    POLL_FETCH_COUNT,
)
from .imap_client import ImapAuthError, ImapClient, ImapClientError

_LOGGER = logging.getLogger(__name__)

_FOLDER_REFRESH_INTERVAL = 86400


@dataclass
class EmailData:
    """Holds the polled email state for one account/folder."""

    emails: list[dict[str, Any]] = field(default_factory=list)
    unread_count: int = 0
    total_count: int = 0
    folders: list[str] = field(default_factory=list)

    @property
    def latest_email(self) -> dict[str, Any] | None:
        """Return the most-recent email, or None if empty."""
        return self.emails[0] if self.emails else None

    @property
    def latest_uid(self) -> str | None:
        """UID of the most-recent email (used to detect new mail)."""
        latest = self.latest_email
        return latest["uid"] if latest else None


class EmailDataUpdateCoordinator(DataUpdateCoordinator[EmailData]):
    """Manages IMAP IDLE push updates with a 15-min fallback poll."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        oauth_session: OAuth2Session,
        email_address: str,
        imap_host: str,
        imap_port: int,
        folder: str,
        scan_interval: int,
    ) -> None:
        self.oauth_session = oauth_session
        self._email = email_address
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._folder = folder
        self._scan_interval = scan_interval
        self._last_uid: str | None = None
        self.last_success_time: datetime | None = None
        self._idle_task: asyncio.Task | None = None
        self._cached_folders: list[str] = []
        self._folders_fetched_at: float = 0.0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{email_address}",
            update_interval=timedelta(seconds=IDLE_FALLBACK_POLL_INTERVAL),
        )

    @property
    def folder(self) -> str:
        """Return the monitored folder name."""
        return self._folder

    @property
    def scan_interval(self) -> int:
        """Return the configured polling interval in seconds."""
        return self._scan_interval

    async def _async_ensure_fresh_token(self) -> str:
        """Proactively refresh OAuth token and return the access token string."""
        try:
            token: dict[str, Any] = self.oauth_session.token
            expires_in = token.get("expires_at", 0) - time.time()
            _LOGGER.debug("Token expires in %.0fs for %s", expires_in, self._email)
            if expires_in < 600 and self.config_entry is not None:
                _LOGGER.debug(
                    "Proactively refreshing token for %s (%.0fs remaining)",
                    self._email,
                    expires_in,
                )
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
                "Token refresh failed for %s: %s: %s",
                self._email,
                type(err).__name__,
                err,
            )
            raise ConfigEntryAuthFailed(
                f"Token refresh failed for {self._email}: {type(err).__name__}: {err}"
            ) from err
        raw = self.oauth_session.token["access_token"]
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def _async_fetch_data(self, client: ImapClient) -> EmailData:
        """Fetch current email state from an already-connected client."""
        status = await client.get_folder_status(self._folder)
        if time.monotonic() - self._folders_fetched_at > _FOLDER_REFRESH_INTERVAL:
            self._cached_folders = await client.list_folders()
            self._folders_fetched_at = time.monotonic()
        emails = await client.search_emails(self._folder, "ALL", POLL_FETCH_COUNT)
        self.last_success_time = datetime.now(timezone.utc)
        _LOGGER.debug(
            "Fetched %d emails for %s (%d unread)",
            len(emails),
            self._email,
            status.get("unseen", 0),
        )
        return EmailData(
            emails=emails,
            unread_count=status.get("unseen", 0),
            total_count=status.get("messages", 0),
            folders=self._cached_folders,
        )

    async def _async_update_data(self) -> EmailData:
        """Short-lived connection poll; fallback when IDLE is not running."""
        access_token = await self._async_ensure_fresh_token()
        try:
            async with ImapClient(self._imap_host, self._imap_port) as client:
                await client.connect(self._email, access_token)
                data = await self._async_fetch_data(client)
        except ImapAuthError as err:
            _LOGGER.warning("IMAP auth error for %s: %s", self._email, err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:
            _LOGGER.warning(
                "IMAP error for %s: %s: %s", self._email, type(err).__name__, err
            )
            raise UpdateFailed(
                f"IMAP error for {self._email}: {type(err).__name__}: {err}"
            ) from err

        self._fire_new_email_event(data)
        return data

    def start_idle(self) -> None:
        """Start the IDLE background task (idempotent)."""
        if self._idle_task is not None:
            return
        self._idle_task = self.hass.async_create_background_task(
            self._async_idle_loop(),
            name=f"{DOMAIN}:idle:{self._email}",
        )
        _LOGGER.debug("IDLE task started for %s", self._email)

    async def stop_idle(self) -> None:
        """Cancel the IDLE task and wait for clean shutdown."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
            _LOGGER.debug("IDLE task stopped for %s", self._email)

    async def _async_idle_loop(self) -> None:
        """Outer IDLE loop: reconnect with exponential backoff on transient errors."""
        _LOGGER.debug("IDLE loop starting for %s", self._email)
        reconnect_attempt = 0
        while True:
            _LOGGER.debug("IDLE attempt %d for %s", reconnect_attempt, self._email)
            reconnect_attempt = await self._async_idle_attempt(reconnect_attempt)

    async def _async_idle_attempt(self, reconnect_attempt: int) -> int:
        """Run one session attempt; return updated reconnect counter."""
        try:
            await self._async_run_idle_session()
        except ConfigEntryAuthFailed:
            _LOGGER.error("IDLE auth permanently failed for %s", self._email)
            raise
        except asyncio.CancelledError:
            raise
        except (OSError, aioimaplib.AioImapException, ImapClientError) as err:
            idx = min(reconnect_attempt, len(IDLE_RECONNECT_DELAYS) - 1)
            delay = IDLE_RECONNECT_DELAYS[idx]
            _LOGGER.warning(
                "IDLE error for %s: %s: %s — reconnecting in %ds",
                self._email,
                type(err).__name__,
                err,
                delay,
            )
            await asyncio.sleep(delay)
            return reconnect_attempt + 1
        else:
            return 0  # clean exit (token refresh) — reset backoff

    async def _async_run_idle_session(self) -> None:
        """One IDLE session: connect, fetch, then IDLE until token nears expiry."""
        _LOGGER.debug("IDLE session: refreshing token for %s", self._email)
        access_token = await self._async_ensure_fresh_token()
        token_expires_at: float = self.oauth_session.token.get("expires_at", 0)
        _LOGGER.debug(
            "IDLE session: connecting for %s (token valid for %.0fs)",
            self._email,
            token_expires_at - time.time(),
        )

        client = ImapClient(self._imap_host, self._imap_port)
        try:
            await client.connect(self._email, access_token)
            _LOGGER.debug("IDLE session: connected for %s", self._email)

            while True:
                if time.time() > token_expires_at - 600:
                    _LOGGER.debug(
                        "Token nearing expiry for %s — reconnecting IDLE", self._email
                    )
                    return

                data = await self._async_fetch_data(client)
                self.async_set_updated_data(data)
                self._fire_new_email_event(data)

                time_until_expiry = token_expires_at - time.time()
                idle_timeout = min(
                    float(IDLE_PUSH_WAIT_TIMEOUT),
                    max(60.0, time_until_expiry - 60),
                )
                _LOGGER.debug(
                    "IDLE waiting for %s (timeout=%.0fs)", self._email, idle_timeout
                )
                push_lines = await client.idle_wait(idle_timeout)

                if push_lines and any(
                    b"EXISTS" in line or b"EXPUNGE" in line for line in push_lines
                ):
                    _LOGGER.debug("IDLE push for %s: %s", self._email, push_lines)
                elif push_lines:
                    _LOGGER.debug(
                        "IDLE push ignored for %s: %s", self._email, push_lines
                    )
                else:
                    _LOGGER.debug("IDLE timeout for %s", self._email)

        except ImapAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        finally:
            await client.disconnect()

    def _fire_new_email_event(self, data: EmailData) -> None:
        """Fire EVENT_NEW_EMAIL when the latest UID has changed."""
        new_uid = data.latest_uid
        if new_uid and new_uid != self._last_uid:
            if self._last_uid is not None:
                latest = data.latest_email or {}
                self.hass.bus.async_fire(
                    EVENT_NEW_EMAIL,
                    {
                        "email_address": self._email,
                        "account": self._email,
                        "config_entry_id": (
                            self.config_entry.entry_id if self.config_entry else None
                        ),
                        "folder": self._folder,
                        "uid": latest.get("uid"),
                        "message_id": latest.get("message_id"),
                        "subject": latest.get("subject"),
                        "sender": latest.get("sender"),
                        "sender_name": latest.get("sender_name"),
                        "sender_email": latest.get("sender_email"),
                        "date": latest.get("date"),
                        "preview": latest.get("preview", ""),
                    },
                )
            self._last_uid = new_uid


def coordinator_from_entry(
    hass: HomeAssistant, entry_id: str
) -> EmailDataUpdateCoordinator | None:
    """Return the coordinator for entry_id, or None if not found."""
    return hass.data.get(DOMAIN, {}).get(entry_id)
