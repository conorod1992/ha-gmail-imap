"""Regression tests for Email HA OAuth lifecycle handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
from yarl import URL

from custom_components.email_ha import _connect_for_call, _options_update_listener
from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator
from custom_components.email_ha.oauth import EmailHAOAuth2Session
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2Implementation,
    OAuth2Session,
)


def _request_info() -> RequestInfo:
    url = URL("https://oauth2.googleapis.com/token")
    headers = CIMultiDictProxy(CIMultiDict())
    return RequestInfo(url, "POST", headers, url)


def _oauth_http_error(status: int) -> ClientResponseError:
    return ClientResponseError(_request_info(), (), status=status)


def _oauth_session(refresh_side_effect=None, *, refresh_result=None):
    config_entries = SimpleNamespace(
        async_update_entry=Mock(),
        async_reload=AsyncMock(),
    )
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={
            "token": {
                "access_token": "old-token",
                "refresh_token": "refresh-token",
                "expires_at": 0,
            }
        },
        async_start_reauth=Mock(),
        async_start_reauth_if_available=Mock(),
    )
    implementation = SimpleNamespace(
        async_refresh_token=AsyncMock(
            side_effect=refresh_side_effect,
            return_value=refresh_result
            or {
                "access_token": "new-token",
                "refresh_token": "refresh-token",
                "expires_at": 9999999999,
            },
        )
    )
    return (
        EmailHAOAuth2Session(
            cast(HomeAssistant, hass),
            cast(ConfigEntry, entry),
            cast(AbstractOAuth2Implementation, implementation),
        ),
        hass,
        entry,
        implementation,
    )


@pytest.mark.asyncio
async def test_successful_token_refresh_does_not_reload_entry() -> None:
    """Normal OAuth token persistence must not reload Email HA."""
    session, hass, entry, implementation = _oauth_session()

    await session.async_ensure_token_valid()

    implementation.async_refresh_token.assert_awaited_once()
    hass.config_entries.async_update_entry.assert_called_once()
    hass.config_entries.async_reload.assert_not_awaited()
    entry.async_start_reauth.assert_not_called()
    entry.async_start_reauth_if_available.assert_not_called()


@pytest.mark.asyncio
async def test_options_listener_ignores_data_only_updates() -> None:
    """The config-entry listener reacts only to user-managed options changes."""
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=AsyncMock()))
    entry = SimpleNamespace(entry_id="entry-1", options={"folder": "INBOX"})
    listener = _options_update_listener(dict(entry.options))

    await listener(cast(HomeAssistant, hass), cast(ConfigEntry, entry))
    hass.config_entries.async_reload.assert_not_awaited()

    entry.options = {"folder": "Receipts"}
    await listener(cast(HomeAssistant, hass), cast(ConfigEntry, entry))
    hass.config_entries.async_reload.assert_awaited_once_with("entry-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_legacy_transient_refresh_failure_does_not_start_reauth(
    monkeypatch, status: int
) -> None:
    """Older HA releases map temporary OAuth failures onto retry semantics."""
    monkeypatch.setattr(
        "custom_components.email_ha.oauth._HAS_NATIVE_OAUTH_ERROR_SEMANTICS", False
    )
    session, _hass, entry, _implementation = _oauth_session(
        refresh_side_effect=_oauth_http_error(status)
    )

    with pytest.raises(ConfigEntryNotReady):
        await session.async_ensure_token_valid()

    entry.async_start_reauth.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_reauth_refresh_failure_starts_reauth(monkeypatch) -> None:
    """Older HA releases map rejected refresh tokens onto reauthentication."""
    monkeypatch.setattr(
        "custom_components.email_ha.oauth._HAS_NATIVE_OAUTH_ERROR_SEMANTICS", False
    )
    session, hass, entry, _implementation = _oauth_session(
        refresh_side_effect=_oauth_http_error(401)
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await session.async_ensure_token_valid()

    entry.async_start_reauth.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_coordinator_preserves_transient_oauth_exception() -> None:
    """Coordinator refresh must not convert a temporary OAuth failure to auth failed."""
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator.oauth_session = cast(
        OAuth2Session,
        SimpleNamespace(
            async_ensure_token_valid=AsyncMock(
                side_effect=ConfigEntryNotReady("temporary OAuth failure")
            ),
            token={"access_token": "unused"},
        ),
    )

    with pytest.raises(ConfigEntryNotReady):
        await coordinator._async_ensure_fresh_token()  # noqa: SLF001


@pytest.mark.asyncio
async def test_idle_retries_transient_oauth_failure(monkeypatch) -> None:
    """IDLE reconnects after a temporary token endpoint failure."""
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = "user@example.com"  # noqa: SLF001
    coordinator._async_run_idle_session = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=ConfigEntryNotReady("temporary OAuth failure")
    )
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.email_ha.coordinator.asyncio.sleep", sleep)

    attempt = await coordinator._async_idle_attempt(0)  # noqa: SLF001

    assert attempt == 1
    sleep.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_action_reports_transient_oauth_failure_as_retryable() -> None:
    """Explicit actions no longer tell users to reauthenticate for a temporary outage."""
    coordinator = SimpleNamespace(
        oauth_session=SimpleNamespace(
            async_ensure_token_valid=AsyncMock(
                side_effect=ConfigEntryNotReady("temporary OAuth failure")
            )
        )
    )

    with pytest.raises(HomeAssistantError, match="try again later"):
        await _connect_for_call(cast(EmailDataUpdateCoordinator, coordinator))


@pytest.mark.asyncio
async def test_action_reports_real_reauth_failure_as_reauth() -> None:
    """Explicit actions retain clear guidance when Google rejects the refresh token."""
    coordinator = SimpleNamespace(
        oauth_session=SimpleNamespace(
            async_ensure_token_valid=AsyncMock(
                side_effect=ConfigEntryAuthFailed("refresh token rejected")
            )
        )
    )

    with pytest.raises(HomeAssistantError, match="reauthenticate"):
        await _connect_for_call(cast(EmailDataUpdateCoordinator, coordinator))
