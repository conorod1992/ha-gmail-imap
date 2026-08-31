"""OAuth helpers for Email HA."""

from __future__ import annotations

import time

from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

_MIN_TOKEN_LIFETIME_SECONDS = 10 * 60


class EmailHAOAuth2Session(OAuth2Session):
    """Refresh Gmail OAuth tokens before opening a long-lived IMAP IDLE session."""

    @property
    def valid_token(self) -> bool:
        """Return whether the token has enough lifetime for the next IDLE lease."""
        return (
            float(self.token.get("expires_at", 0))
            > time.time() + _MIN_TOKEN_LIFETIME_SECONDS
        )
