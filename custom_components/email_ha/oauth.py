"""OAuth helpers for Email HA."""

from __future__ import annotations

from http import HTTPStatus
import time

from aiohttp import ClientError, ClientResponseError

from homeassistant import exceptions as ha_exceptions
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

_MIN_TOKEN_LIFETIME_SECONDS = 10 * 60
_HAS_NATIVE_OAUTH_ERROR_SEMANTICS = hasattr(ha_exceptions, "OAuth2TokenRequestError")


class EmailHAOAuth2Session(OAuth2Session):
    """Refresh Gmail OAuth tokens before opening a long-lived IMAP IDLE session."""

    @property
    def valid_token(self) -> bool:
        """Return whether the token has enough lifetime for the next IDLE lease."""
        return (
            float(self.token.get("expires_at", 0))
            > time.time() + _MIN_TOKEN_LIFETIME_SECONDS
        )

    async def async_ensure_token_valid(self) -> None:
        """Refresh the token while preserving current HA OAuth failure semantics."""
        if _HAS_NATIVE_OAUTH_ERROR_SEMANTICS:
            await super().async_ensure_token_valid()
            return

        # Home Assistant 2026.2 and earlier surfaced aiohttp errors directly from
        # OAuth refreshes. Translate those to the same retry/reauth semantics used
        # by newer Home Assistant releases without changing the minimum HA version.
        try:
            await super().async_ensure_token_valid()
        except ClientResponseError as err:
            if err.status == HTTPStatus.TOO_MANY_REQUESTS or 500 <= err.status <= 599:
                raise ConfigEntryNotReady(
                    "Temporary Gmail OAuth refresh failure; will retry"
                ) from err
            if 400 <= err.status <= 499:
                self.config_entry.async_start_reauth(self.hass)
                raise ConfigEntryAuthFailed(
                    "Gmail authorization expired or was revoked"
                ) from err
            raise ConfigEntryNotReady(
                "Temporary Gmail OAuth refresh failure; will retry"
            ) from err
        except ClientError as err:
            raise ConfigEntryNotReady(
                "Temporary Gmail OAuth refresh failure; will retry"
            ) from err
