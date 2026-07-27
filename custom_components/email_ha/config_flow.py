"""Config flow for Email IMAP (Gmail via OAuth2)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import config_entry_oauth2_flow, selector

from .const import (
    CONF_EMAIL,
    CONF_FOLDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_SENSORS,
    DEFAULT_FOLDER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GMAIL_SCOPES,
    MAX_SEARCH_SENSORS,
)
from .search import (
    GMAIL_CATEGORIES,
    GMAIL_INBOX_SENSOR_PRESETS,
    IMPORTANT_STATES,
    READ_STATES,
    STARRED_STATES,
    build_structured_search_tokens,
    normalize_structured_filters,
)

_LOGGER = logging.getLogger(__name__)

STEP_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FOLDER, default=DEFAULT_FOLDER): str,
        vol.Optional(
            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=30, max=3600, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }
)


def _select(values: tuple[str, ...]) -> selector.SelectSelector:
    """Return a dropdown selector for fixed search choices."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(values), mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


def _search_sensor_schema() -> vol.Schema:
    """Return the UI schema for one structured count sensor."""
    return vol.Schema(
        {
            vol.Required("name"): selector.TextSelector(),
            vol.Optional(CONF_FOLDER, default=DEFAULT_FOLDER): selector.TextSelector(),
            vol.Optional("from"): selector.TextSelector(),
            vol.Optional("to"): selector.TextSelector(),
            vol.Optional("cc"): selector.TextSelector(),
            vol.Optional("subject"): selector.TextSelector(),
            vol.Optional("body"): selector.TextSelector(),
            vol.Optional("text"): selector.TextSelector(),
            vol.Optional("read_state", default="any"): _select(READ_STATES),
            vol.Optional("starred_state", default="any"): _select(STARRED_STATES),
            vol.Optional("important_state", default="any"): _select(IMPORTANT_STATES),
            vol.Optional("gmail_category", default="any"): _select(
                ("any", *GMAIL_CATEGORIES)
            ),
            vol.Optional("since"): selector.DateSelector(),
            vol.Optional("before"): selector.DateSelector(),
            vol.Optional("on"): selector.DateSelector(),
        }
    )


def _gmail_sensor_preset_schema() -> vol.Schema:
    """Return the multi-select schema for optional Gmail Inbox sensors."""
    options = [
        selector.SelectOptionDict(value=key, label=str(preset["name"]))
        for key, preset in GMAIL_INBOX_SENSOR_PRESETS.items()
    ]
    return vol.Schema(
        {
            vol.Required("presets"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow for Email IMAP."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._email: str = ""
        self._token_data: dict[str, Any] = {}

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Scopes and params appended to the Google authorize URL."""
        return {
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            # Force the consent screen so Google always returns a refresh token
            "prompt": "consent",
            # Pre-select the account the user entered
            "login_hint": self._email,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the Gmail address, then hand off to OAuth2."""
        if user_input is None:
            implementations = await config_entry_oauth2_flow.async_get_implementations(
                self.hass, DOMAIN
            )
            if not implementations:
                return self.async_abort(reason="missing_credentials")

            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            )

        self._email = user_input[CONF_EMAIL].strip().lower()
        await self.async_set_unique_id(self._email)
        self._abort_if_unique_id_configured()

        return await self.async_step_pick_implementation()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication."""
        self._email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show reauth confirmation then re-run OAuth."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Intercept after token exchange to collect mailbox settings or update token."""
        self._token_data = data
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, **data},
            )
            return self.async_abort(reason="reauth_successful")
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect folder / polling preferences before creating the entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="settings",
                data_schema=STEP_SETTINGS_SCHEMA,
            )

        return self.async_create_entry(
            title=self._email,
            data={
                **self._token_data,
                CONF_EMAIL: self._email,
                CONF_FOLDER: user_input[CONF_FOLDER],
                CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EmailIMAPOptionsFlow()


class EmailIMAPOptionsFlow(OptionsFlow):
    """Options flow for mailbox settings and optional count sensors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        del user_input
        menu_options = ["mailbox", "add_gmail_sensor", "add_search_sensor"]
        if self.config_entry.options.get(CONF_SEARCH_SENSORS):
            menu_options.append("remove_search_sensor")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_mailbox(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the monitored folder and polling settings."""
        if user_input is not None:
            return self._save_options(user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FOLDER, default=current.get(CONF_FOLDER, DEFAULT_FOLDER)
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30, max=3600, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="mailbox", data_schema=schema)

    async def async_step_add_search_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one optional structured search/count sensor."""
        sensors = list(self.config_entry.options.get(CONF_SEARCH_SENSORS, []))
        if len(sensors) >= MAX_SEARCH_SENSORS:
            return self.async_abort(reason="too_many_search_sensors")
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input["name"]).strip()
            folder = str(user_input[CONF_FOLDER]).strip()
            if not name or not folder:
                errors["base"] = "invalid_search_filters"
            else:
                try:
                    filters = normalize_structured_filters(user_input)
                    build_structured_search_tokens(filters)
                except ValueError:
                    errors["base"] = "invalid_search_filters"
                else:
                    sensors.append(
                        {
                            "id": uuid4().hex,
                            "name": name,
                            CONF_FOLDER: folder,
                            "filters": filters,
                        }
                    )
                    return self._save_options({CONF_SEARCH_SENSORS: sensors})
        return self.async_show_form(
            step_id="add_search_sensor",
            data_schema=_search_sensor_schema(),
            errors=errors,
        )

    async def async_step_add_gmail_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one or more reliable Gmail Inbox sensor presets."""
        sensors = list(self.config_entry.options.get(CONF_SEARCH_SENSORS, []))
        if len(sensors) >= MAX_SEARCH_SENSORS:
            return self.async_abort(reason="too_many_search_sensors")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input["presets"]
            if isinstance(selected, str):
                selected = [selected]
            configured = {
                str(sensor.get("preset")) for sensor in sensors if sensor.get("preset")
            }
            new_presets = [
                str(key)
                for key in selected
                if key in GMAIL_INBOX_SENSOR_PRESETS and key not in configured
            ]
            if not new_presets:
                errors["base"] = "gmail_sensors_already_configured"
            elif len(sensors) + len(new_presets) > MAX_SEARCH_SENSORS:
                return self.async_abort(reason="too_many_search_sensors")
            else:
                for key in new_presets:
                    preset = GMAIL_INBOX_SENSOR_PRESETS[key]
                    filters = dict(preset["filters"])
                    build_structured_search_tokens(filters)
                    sensors.append(
                        {
                            "id": uuid4().hex,
                            "name": str(preset["name"]),
                            CONF_FOLDER: DEFAULT_FOLDER,
                            "filters": filters,
                            "preset": key,
                        }
                    )
                return self._save_options({CONF_SEARCH_SENSORS: sensors})

        return self.async_show_form(
            step_id="add_gmail_sensor",
            data_schema=_gmail_sensor_preset_schema(),
            errors=errors,
        )

    async def async_step_remove_search_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one configured search/count sensor."""
        sensors = list(self.config_entry.options.get(CONF_SEARCH_SENSORS, []))
        if user_input is not None:
            selected = user_input["sensor_id"]
            return self._save_options(
                {
                    CONF_SEARCH_SENSORS: [
                        sensor for sensor in sensors if sensor.get("id") != selected
                    ]
                }
            )
        choices = [
            selector.SelectOptionDict(value=sensor["id"], label=sensor["name"])
            for sensor in sensors
        ]
        return self.async_show_form(
            step_id="remove_search_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required("sensor_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    def _save_options(self, changes: dict[str, Any]) -> ConfigFlowResult:
        """Save changed options without discarding unrelated options."""
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **changes}
        )
