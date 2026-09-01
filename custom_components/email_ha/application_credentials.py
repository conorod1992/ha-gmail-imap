"""application_credentials platform for Email IMAP (Gmail)."""

from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return Gmail's OAuth2 authorization server."""
    return AuthorizationServer(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
    )


async def async_get_description_placeholders(
    hass: HomeAssistant,
) -> dict[str, str]:
    """Link the credentials dialog to exact setup prerequisites."""
    return {
        "documentation_url": "https://github.com/conorod1992/ha-gmail-imap#connect-your-gmail-account",
        "consent_url": "https://console.cloud.google.com/auth/overview",
        "credentials_url": "https://console.cloud.google.com/apis/credentials",
    }
