"""Beginner-first OAuth setup and state-based account management."""

from __future__ import annotations

from copy import deepcopy
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from .const import (
    CONF_CATCH_UP,
    CONF_CUSTOM_SENSORS,
    CONF_EMAIL,
    CONF_EMAIL_WATCHES,
    CONF_FOLDER,
    CONF_GMAIL_ENTITIES,
    CONF_MONITORED_FOLDER,
    DEFAULT_FOLDER,
    DOMAIN,
    GMAIL_SCOPES,
    MAX_CUSTOM_SENSORS,
    MAX_EMAIL_WATCHES,
)
from .coordinator import coordinator_from_entry
from .gmail import (
    DEFAULT_GMAIL_ENTITIES,
    GMAIL_ENTITIES,
    GMAIL_ENTITY_DEFINITIONS,
    enabled_entities_for_entry,
)
from .imap_client import ImapClientError
from .search import (
    ATTACHMENT_STATES,
    GMAIL_CATEGORIES,
    IMPORTANT_STATES,
    READ_STATES,
    RELATIVE_DATE_RANGES,
    STARRED_STATES,
    build_structured_search_tokens,
    normalize_structured_filters,
    summarize_structured_filters,
    validate_imap_folder,
)

_LOGGER = logging.getLogger(__name__)

_COMMON_FILTER_FIELDS = (
    "from",
    "subject",
    "read_state",
    "gmail_category",
    "important_state",
    "starred_state",
    "attachment_state",
)
_ADVANCED_FILTER_FIELDS = (
    "to",
    "cc",
    "body",
    "text",
    "attachment_filename",
    "relative_date",
    "since",
    "before",
    "on",
)


def _select(values: tuple[str, ...], translation_key: str) -> selector.SelectSelector:
    """Return one translated dropdown."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(values),
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key=translation_key,
        )
    )


def _gmail_entities_schema(default: list[str]) -> vol.Schema:
    """Return the state-based fixed entity selector."""
    return vol.Schema(
        {
            vol.Required(CONF_GMAIL_ENTITIES, default=default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[definition.key for definition in GMAIL_ENTITY_DEFINITIONS],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                    translation_key="gmail_entities",
                )
            )
        }
    )


def _friendly_folder_name(folder: str) -> str:
    """Return a readable label while preserving the real identifier."""
    if folder.upper() == DEFAULT_FOLDER:
        return "Inbox"
    display = folder.rsplit("/", 1)[-1]
    return f"{display} ({folder})" if display != folder else folder


def _folder_selector(folders: list[str]) -> selector.SelectSelector:
    """Offer discovered folders while allowing an advanced arbitrary value."""
    options = [
        selector.SelectOptionDict(value=folder, label=_friendly_folder_name(folder))
        for folder in dict.fromkeys([DEFAULT_FOLDER, *folders])
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _custom_common_schema(
    folders: list[str], values: dict[str, Any], *, is_watch: bool = False
) -> vol.Schema:
    """Return the approachable first half of a custom count sensor form."""
    filters = values.get("filters", {})
    has_advanced = any(filters.get(field) for field in _ADVANCED_FILTER_FIELDS)
    fields: dict[Any, Any] = {
        vol.Required("name", default=values.get("name", "")): selector.TextSelector(),
        vol.Required(
            CONF_FOLDER, default=values.get(CONF_FOLDER, DEFAULT_FOLDER)
        ): _folder_selector(folders),
        vol.Optional("from", default=filters.get("from", "")): selector.TextSelector(),
        vol.Optional(
            "subject", default=filters.get("subject", "")
        ): selector.TextSelector(),
        vol.Optional("read_state", default=filters.get("read_state", "any")): _select(
            READ_STATES, "read_state"
        ),
        vol.Optional(
            "gmail_category", default=filters.get("gmail_category", "any")
        ): _select(("any", *GMAIL_CATEGORIES), "gmail_category"),
        vol.Optional(
            "important_state", default=filters.get("important_state", "any")
        ): _select(IMPORTANT_STATES, "important_state"),
        vol.Optional(
            "starred_state", default=filters.get("starred_state", "any")
        ): _select(STARRED_STATES, "starred_state"),
        vol.Optional(
            "attachment_state", default=filters.get("attachment_state", "any")
        ): _select(ATTACHMENT_STATES, "attachment_state"),
        vol.Optional("more_filters", default=has_advanced): selector.BooleanSelector(),
        vol.Optional("test_filter", default=False): selector.BooleanSelector(),
    }
    if is_watch:
        fields[vol.Required("enabled", default=values.get("enabled", True))] = (
            selector.BooleanSelector()
        )
        fields[
            vol.Optional(CONF_CATCH_UP, default=values.get(CONF_CATCH_UP, False))
        ] = selector.BooleanSelector()
    return vol.Schema(fields)


def _custom_advanced_schema(values: dict[str, Any]) -> vol.Schema:
    """Return the optional second half of the custom count sensor form."""
    filters = values.get("filters", {})
    fields: dict[vol.Optional, Any] = {}
    for field in _ADVANCED_FILTER_FIELDS:
        if field == "relative_date":
            fields[vol.Optional(field, default=filters.get(field, "any"))] = _select(
                RELATIVE_DATE_RANGES, "relative_date"
            )
            continue
        marker = (
            vol.Optional(field, default=filters[field])
            if filters.get(field)
            else vol.Optional(field)
        )
        fields[marker] = (
            selector.DateSelector()
            if field in {"since", "before", "on"}
            else selector.TextSelector()
        )
    fields[vol.Optional("test_filter", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


def _custom_sensor_summary(sensor_config: dict[str, Any]) -> str:
    """Build an identifying management label from every supported filter."""
    summary = summarize_structured_filters(
        sensor_config.get("filters", {}),
        folder=str(sensor_config.get(CONF_FOLDER, DEFAULT_FOLDER)),
        short=True,
    )
    disabled = " — Disabled" if sensor_config.get("enabled", True) is False else ""
    catch_up = " · Catch up after restart" if sensor_config.get(CONF_CATCH_UP) else ""
    return (
        f"{sensor_config.get('name', 'Custom sensor')} — {summary}{catch_up}{disabled}"
    )


def _full_rule_summary(draft: dict[str, Any]) -> str:
    """Return the complete deterministic explanation shown on edit forms."""
    summary = summarize_structured_filters(
        draft.get("filters", {}),
        folder=str(draft.get(CONF_FOLDER, DEFAULT_FOLDER)),
    )
    if draft.get(CONF_CATCH_UP):
        return f"{summary} · Catch up after Home Assistant restarts"
    return summary


def _ordered_gmail_entities(selected: set[str]) -> list[str]:
    """Return valid selected entities in the canonical UI order."""
    return [
        definition.key
        for definition in GMAIL_ENTITY_DEFINITIONS
        if definition.key in selected
    ]


def _upsert_custom_sensor(
    sensors: list[dict[str, Any]],
    draft: dict[str, Any],
    *,
    sensor_id: str,
    replace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return custom sensor state after an add, duplicate, or edit."""
    saved = {**deepcopy(draft), "id": sensor_id}
    if replace_id is None:
        return [*sensors, saved]
    return [saved if sensor.get("id") == replace_id else sensor for sensor in sensors]


def _delete_custom_sensor(
    sensors: list[dict[str, Any]], sensor_id: str | None
) -> list[dict[str, Any]]:
    """Return custom sensor state without one selected object."""
    return [sensor for sensor in sensors if sensor.get("id") != sensor_id]


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Configure one Gmail account through Home Assistant OAuth helpers."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        super().__init__()
        self._email = ""
        self._token_data: dict[str, Any] = {}

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Request the Gmail IMAP OAuth scope and a reusable refresh token."""
        return {
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": self._email,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the Gmail address before redirecting to Google."""
        if user_input is None:
            implementations = await config_entry_oauth2_flow.async_get_implementations(
                self.hass, DOMAIN
            )
            if not implementations:
                return self.async_abort(reason="missing_credentials")
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_EMAIL): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.EMAIL
                            )
                        )
                    }
                ),
            )

        self._email = str(user_input[CONF_EMAIL]).strip().lower()
        await self.async_set_unique_id(self._email)
        self._abort_if_unique_id_configured()
        return await self.async_step_pick_implementation()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for the existing address."""
        self._email = str(entry_data.get(CONF_EMAIL, ""))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm before returning to Google's OAuth page."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Continue to entity selection or update an existing token."""
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data, reason="reauth_successful"
            )
        self._token_data = data
        return await self.async_step_sensors()

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select recommended Gmail entities using user-facing concepts."""
        if user_input is None:
            return self.async_show_form(
                step_id="sensors",
                data_schema=_gmail_entities_schema(list(DEFAULT_GMAIL_ENTITIES)),
            )
        selected = _ordered_gmail_entities(set(user_input[CONF_GMAIL_ENTITIES]))
        return self.async_create_entry(
            title=self._email,
            data={**self._token_data, CONF_EMAIL: self._email},
            options={
                CONF_GMAIL_ENTITIES: selected,
                CONF_MONITORED_FOLDER: DEFAULT_FOLDER,
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the account management flow."""
        return EmailHAOptionsFlow()


class EmailHAOptionsFlow(OptionsFlow):
    """Manage fixed entities, custom sensors, and advanced account settings."""

    def __init__(self) -> None:
        self._custom_mode = ""
        self._custom_id: str | None = None
        self._custom_draft: dict[str, Any] = {}
        self._custom_test_after_advanced = False
        self._watch_mode = ""
        self._watch_id: str | None = None
        self._watch_draft: dict[str, Any] = {}
        self._watch_test_after_advanced = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the three progressive-disclosure management areas."""
        del user_input
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "gmail_sensors",
                "custom_sensors",
                "email_watches",
                "advanced_account_settings",
            ],
        )

    async def async_step_gmail_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconcile the complete desired state of fixed Gmail entities."""
        if user_input is None:
            enabled = enabled_entities_for_entry(self.config_entry)
            current = [
                definition.key
                for definition in GMAIL_ENTITY_DEFINITIONS
                if definition.key in enabled
            ]
            return self.async_show_form(
                step_id="gmail_sensors",
                data_schema=_gmail_entities_schema(current),
            )
        selected = {
            str(key)
            for key in user_input[CONF_GMAIL_ENTITIES]
            if str(key) in GMAIL_ENTITIES
        }
        self._reconcile_entity_registry(selected)
        ordered = _ordered_gmail_entities(selected)
        return self._save_options({CONF_GMAIL_ENTITIES: ordered})

    def _reconcile_entity_registry(self, selected: set[str]) -> None:
        """Enable or integration-disable existing fixed registry entries."""
        registry = er.async_get(self.hass)
        for registry_entry in er.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            key = registry_entry.unique_id.removeprefix(
                f"{self.config_entry.entry_id}_"
            )
            if key not in GMAIL_ENTITIES:
                continue
            desired = None if key in selected else RegistryEntryDisabler.INTEGRATION
            if registry_entry.disabled_by != desired:
                registry.async_update_entity(
                    registry_entry.entity_id, disabled_by=desired
                )

    def _folders(self) -> list[str]:
        """Return folders discovered by the loaded coordinator."""
        coordinator = coordinator_from_entry(self.hass, self.config_entry.entry_id)
        if coordinator and coordinator.data:
            return list(coordinator.data.folders)
        return [DEFAULT_FOLDER]

    async def async_step_advanced_account_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the only retained IMAP-specific account preference."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                monitored_folder = validate_imap_folder(
                    user_input[CONF_MONITORED_FOLDER]
                )
            except ValueError:
                errors["base"] = "invalid_folder"
            else:
                return self._save_options({CONF_MONITORED_FOLDER: monitored_folder})
        current = self.config_entry.options.get(CONF_MONITORED_FOLDER, DEFAULT_FOLDER)
        return self.async_show_form(
            step_id="advanced_account_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MONITORED_FOLDER, default=current
                    ): _folder_selector(self._folders())
                }
            ),
            errors=errors,
        )

    def _custom_sensors(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_CUSTOM_SENSORS, []))

    async def async_step_custom_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show one identifying management list for all custom sensors."""
        sensors = self._custom_sensors()
        if user_input is None:
            choices = [
                selector.SelectOptionDict(value="add", label="Add a custom sensor")
            ]
            for sensor_config in sensors:
                sensor_id = sensor_config["id"]
                choices.append(
                    selector.SelectOptionDict(
                        value=f"manage:{sensor_id}",
                        label=_custom_sensor_summary(sensor_config),
                    )
                )
            return self.async_show_form(
                step_id="custom_sensors",
                data_schema=vol.Schema(
                    {
                        vol.Required("manage_action"): selector.SelectSelector(
                            selector.SelectSelectorConfig(options=choices)
                        )
                    }
                ),
            )

        action_value = str(user_input["manage_action"])
        if action_value == "add":
            if len(sensors) >= MAX_CUSTOM_SENSORS:
                return self.async_abort(reason="too_many_custom_sensors")
            self._custom_mode = "add"
            self._custom_id = None
            self._custom_draft = {}
            return await self.async_step_custom_sensor_common()

        action, sensor_id = action_value.split(":", 1)
        selected = next(
            (sensor for sensor in sensors if sensor.get("id") == sensor_id), None
        )
        if selected is None:
            return self.async_abort(reason="custom_sensor_not_found")
        if action == "manage":
            self._custom_mode = ""
            self._custom_id = sensor_id
            self._custom_draft = deepcopy(selected)
            return await self.async_step_custom_sensor_action()
        self._custom_mode = action
        self._custom_id = sensor_id
        self._custom_draft = deepcopy(selected)
        if action == "duplicate":
            self._custom_draft["name"] = f"Copy of {selected['name']}"
            return await self.async_step_custom_sensor_common()
        if action == "delete":
            return await self.async_step_delete_custom_sensor()
        return await self.async_step_custom_sensor_common()

    async def async_step_custom_sensor_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose an operation after selecting one logical sensor."""
        if user_input is None:
            return self.async_show_form(
                step_id="custom_sensor_action",
                data_schema=vol.Schema(
                    {
                        vol.Required("action"): _select(
                            ("edit", "duplicate", "delete"), "manage_action"
                        )
                    }
                ),
            )
        self._custom_mode = str(user_input["action"])
        if self._custom_mode == "delete":
            return await self.async_step_delete_custom_sensor()
        if self._custom_mode == "duplicate":
            self._custom_draft["name"] = f"Copy of {self._custom_draft['name']}"
        return await self.async_step_custom_sensor_common()

    async def async_step_custom_sensor_common(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect name and common Gmail-oriented filters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input["name"]).strip()
            try:
                folder = validate_imap_folder(user_input[CONF_FOLDER])
            except ValueError:
                folder = ""
            if not name or not folder:
                errors["base"] = "invalid_custom_sensor"
            else:
                existing = self._custom_draft.get("filters", {})
                advanced = {
                    key: existing[key]
                    for key in _ADVANCED_FILTER_FIELDS
                    if existing.get(key)
                }
                try:
                    common = normalize_structured_filters(
                        {key: user_input.get(key) for key in _COMMON_FILTER_FIELDS}
                    )
                except ValueError:
                    errors["base"] = "invalid_custom_sensor"
                else:
                    if not user_input.get("more_filters"):
                        advanced = {}
                    self._custom_draft = {
                        **self._custom_draft,
                        "name": name,
                        CONF_FOLDER: folder,
                        "filters": {**advanced, **common},
                    }
                    if user_input.get("more_filters"):
                        self._custom_test_after_advanced = bool(
                            user_input.get("test_filter")
                        )
                        return await self.async_step_custom_sensor_advanced()
                    if user_input.get("test_filter"):
                        return await self.async_step_custom_sensor_preview()
                    return self._finish_custom_sensor()
        return self.async_show_form(
            step_id="custom_sensor_common",
            data_schema=_custom_common_schema(self._folders(), self._custom_draft),
            errors=errors,
            description_placeholders={
                "summary": _full_rule_summary(self._custom_draft)
            },
        )

    async def async_step_custom_sensor_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Preview a draft without saving it or creating an entity."""
        if user_input is None:
            return await self._async_show_preview(
                "custom_sensor_preview", self._custom_draft
            )
        return (
            self._finish_custom_sensor()
            if user_input.get("save")
            else await self.async_step_custom_sensor_common()
        )

    async def async_step_custom_sensor_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect less common text, recipient, and date filters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            common = {
                key: value
                for key, value in self._custom_draft.get("filters", {}).items()
                if key in _COMMON_FILTER_FIELDS
            }
            try:
                advanced = normalize_structured_filters(user_input)
                self._custom_draft["filters"] = {**common, **advanced}
                should_test = self._custom_test_after_advanced or bool(
                    user_input.get("test_filter")
                )
                self._custom_test_after_advanced = False
                if should_test:
                    return await self.async_step_custom_sensor_preview()
                return self._finish_custom_sensor()
            except ValueError:
                errors["base"] = "invalid_custom_sensor"
        return self.async_show_form(
            step_id="custom_sensor_advanced",
            data_schema=_custom_advanced_schema(self._custom_draft),
            errors=errors,
            description_placeholders={
                "summary": _full_rule_summary(self._custom_draft)
            },
            last_step=True,
        )

    def _finish_custom_sensor(self) -> ConfigFlowResult:
        """Validate and persist an add, edit, or duplicate operation."""
        try:
            build_structured_search_tokens(self._custom_draft.get("filters", {}))
        except ValueError:
            return self.async_abort(reason="invalid_custom_sensor")
        sensors = self._custom_sensors()
        if self._custom_mode == "edit":
            sensors = _upsert_custom_sensor(
                sensors,
                self._custom_draft,
                sensor_id=str(self._custom_id),
                replace_id=self._custom_id,
            )
        else:
            if len(sensors) >= MAX_CUSTOM_SENSORS:
                return self.async_abort(reason="too_many_custom_sensors")
            sensors = _upsert_custom_sensor(
                sensors, self._custom_draft, sensor_id=uuid4().hex
            )
        return self._save_options({CONF_CUSTOM_SENSORS: sensors})

    async def async_step_delete_custom_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require explicit confirmation before deleting a custom sensor."""
        if user_input is not None:
            if not user_input.get("confirm"):
                return await self.async_step_custom_sensors()
            registry = er.async_get(self.hass)
            unique_id = f"{self.config_entry.entry_id}_custom_{self._custom_id}"
            if entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id):
                registry.async_remove(entity_id)
            return self._save_options(
                {
                    CONF_CUSTOM_SENSORS: _delete_custom_sensor(
                        self._custom_sensors(), self._custom_id
                    )
                }
            )
        return self.async_show_form(
            step_id="delete_custom_sensor",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "name": str(self._custom_draft.get("name", "Custom sensor"))
            },
        )

    def _email_watches(self) -> list[dict[str, Any]]:
        """Return persisted Email watches for this account."""
        return list(self.config_entry.options.get(CONF_EMAIL_WATCHES, []))

    async def async_step_email_watches(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage watches using the same structured-filter UX as sensors."""
        watches = self._email_watches()
        if user_input is None:
            choices = [
                selector.SelectOptionDict(value="add", label="Add an email watch")
            ]
            for watch in watches:
                watch_id = watch["id"]
                choices.append(
                    selector.SelectOptionDict(
                        value=f"manage:{watch_id}", label=_custom_sensor_summary(watch)
                    )
                )
            return self.async_show_form(
                step_id="email_watches",
                data_schema=vol.Schema(
                    {
                        vol.Required("manage_action"): selector.SelectSelector(
                            selector.SelectSelectorConfig(options=choices)
                        )
                    }
                ),
            )

        action_value = str(user_input["manage_action"])
        if action_value == "add":
            if len(watches) >= MAX_EMAIL_WATCHES:
                return self.async_abort(reason="too_many_email_watches")
            self._watch_mode = "add"
            self._watch_id = None
            self._watch_draft = {}
            return await self.async_step_email_watch_common()
        action, watch_id = action_value.split(":", 1)
        selected = next(
            (watch for watch in watches if watch.get("id") == watch_id), None
        )
        if selected is None:
            return self.async_abort(reason="email_watch_not_found")
        if action == "manage":
            self._watch_mode = ""
            self._watch_id = watch_id
            self._watch_draft = deepcopy(selected)
            return await self.async_step_email_watch_action()
        self._watch_mode = action
        self._watch_id = watch_id
        self._watch_draft = deepcopy(selected)
        if action == "duplicate":
            self._watch_draft["name"] = f"Copy of {selected['name']}"
        if action == "delete":
            return await self.async_step_delete_email_watch()
        return await self.async_step_email_watch_common()

    async def async_step_email_watch_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage one Email watch without mixing actions into its list label."""
        if user_input is None:
            options = (
                ("edit", "duplicate", "delete", "disable")
                if self._watch_draft.get("enabled", True)
                else ("edit", "duplicate", "delete", "enable")
            )
            return self.async_show_form(
                step_id="email_watch_action",
                data_schema=vol.Schema(
                    {vol.Required("action"): _select(options, "manage_action")}
                ),
            )
        action = str(user_input["action"])
        if action in {"enable", "disable"}:
            watches = _upsert_custom_sensor(
                self._email_watches(),
                {**self._watch_draft, "enabled": action == "enable"},
                sensor_id=str(self._watch_id),
                replace_id=self._watch_id,
            )
            return self._save_options({CONF_EMAIL_WATCHES: watches})
        self._watch_mode = action
        if action == "delete":
            return await self.async_step_delete_email_watch()
        if action == "duplicate":
            self._watch_draft["name"] = f"Copy of {self._watch_draft['name']}"
            self._watch_draft["enabled"] = True
        return await self.async_step_email_watch_common()

    async def async_step_email_watch_common(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a watch name, folder, and common filters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input["name"]).strip()
            try:
                folder = validate_imap_folder(user_input[CONF_FOLDER])
                common = normalize_structured_filters(
                    {key: user_input.get(key) for key in _COMMON_FILTER_FIELDS}
                )
            except ValueError:
                folder = ""
                common = {}
            if not name or not folder:
                errors["base"] = "invalid_email_watch"
            else:
                existing = self._watch_draft.get("filters", {})
                advanced = {
                    key: existing[key]
                    for key in _ADVANCED_FILTER_FIELDS
                    if existing.get(key)
                }
                if not user_input.get("more_filters"):
                    advanced = {}
                self._watch_draft = {
                    **self._watch_draft,
                    "name": name,
                    CONF_FOLDER: folder,
                    "filters": {**advanced, **common},
                    "enabled": bool(user_input.get("enabled", True)),
                    CONF_CATCH_UP: bool(user_input.get(CONF_CATCH_UP, False)),
                }
                if user_input.get("more_filters"):
                    self._watch_test_after_advanced = bool(
                        user_input.get("test_filter")
                    )
                    return await self.async_step_email_watch_advanced()
                if user_input.get("test_filter"):
                    return await self.async_step_email_watch_preview()
                return self._finish_email_watch()
        return self.async_show_form(
            step_id="email_watch_common",
            data_schema=_custom_common_schema(
                self._folders(), self._watch_draft, is_watch=True
            ),
            errors=errors,
            description_placeholders={"summary": _full_rule_summary(self._watch_draft)},
        )

    async def async_step_email_watch_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Preview a watch draft without affecting UID baselines or events."""
        if user_input is None:
            return await self._async_show_preview(
                "email_watch_preview", self._watch_draft
            )
        return (
            self._finish_email_watch()
            if user_input.get("save")
            else await self.async_step_email_watch_common()
        )

    async def _async_show_preview(
        self, step_id: str, draft: dict[str, Any]
    ) -> ConfigFlowResult:
        """Search current draft via a short-lived read-only connection."""
        coordinator = coordinator_from_entry(self.hass, self.config_entry.entry_id)
        if coordinator is None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=vol.Schema(
                    {vol.Required("save", default=False): selector.BooleanSelector()}
                ),
                errors={"base": "filter_test_failed"},
            )
        try:
            messages = await coordinator.async_preview_filter(
                draft.get(CONF_FOLDER, DEFAULT_FOLDER), draft.get("filters", {}), 5
            )
            preview = "Showing up to 5 newest matching emails."
            preview += (
                "\nNo matching emails."
                if not messages
                else "\n"
                + "\n".join(
                    f"• {item.get('subject') or '(no subject)'} — {(item.get('sender') or {}).get('address', '')} — {item.get('date') or ''}"
                    for item in messages
                )
            )
            return self.async_show_form(
                step_id=step_id,
                data_schema=vol.Schema(
                    {vol.Required("save", default=False): selector.BooleanSelector()}
                ),
                description_placeholders={"preview": preview},
            )
        except (HomeAssistantError, ImapClientError, ValueError):
            return self.async_show_form(
                step_id=step_id,
                data_schema=vol.Schema(
                    {vol.Required("save", default=False): selector.BooleanSelector()}
                ),
                errors={"base": "filter_test_failed"},
            )

    async def async_step_email_watch_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect advanced filters for an Email watch."""
        errors: dict[str, str] = {}
        if user_input is not None:
            common = {
                key: value
                for key, value in self._watch_draft.get("filters", {}).items()
                if key in _COMMON_FILTER_FIELDS
            }
            try:
                advanced = normalize_structured_filters(user_input)
                self._watch_draft["filters"] = {**common, **advanced}
                should_test = self._watch_test_after_advanced or bool(
                    user_input.get("test_filter")
                )
                self._watch_test_after_advanced = False
                if should_test:
                    return await self.async_step_email_watch_preview()
                return self._finish_email_watch()
            except ValueError:
                errors["base"] = "invalid_email_watch"
        return self.async_show_form(
            step_id="email_watch_advanced",
            data_schema=_custom_advanced_schema(self._watch_draft),
            errors=errors,
            description_placeholders={"summary": _full_rule_summary(self._watch_draft)},
            last_step=True,
        )

    def _finish_email_watch(self) -> ConfigFlowResult:
        """Validate and persist a watch while retaining identity on edits."""
        try:
            build_structured_search_tokens(self._watch_draft.get("filters", {}))
        except ValueError:
            return self.async_abort(reason="invalid_email_watch")
        watches = self._email_watches()
        if self._watch_mode == "edit":
            watches = _upsert_custom_sensor(
                watches,
                self._watch_draft,
                sensor_id=str(self._watch_id),
                replace_id=self._watch_id,
            )
        else:
            if len(watches) >= MAX_EMAIL_WATCHES:
                return self.async_abort(reason="too_many_email_watches")
            watches = _upsert_custom_sensor(
                watches, self._watch_draft, sensor_id=uuid4().hex
            )
        return self._save_options({CONF_EMAIL_WATCHES: watches})

    async def async_step_delete_email_watch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm deletion and remove the corresponding registry entity."""
        if user_input is not None:
            if not user_input.get("confirm"):
                return await self.async_step_email_watches()
            registry = er.async_get(self.hass)
            unique_id = f"{self.config_entry.entry_id}_watch_{self._watch_id}"
            if entity_id := registry.async_get_entity_id("event", DOMAIN, unique_id):
                registry.async_remove(entity_id)
            return self._save_options(
                {
                    CONF_EMAIL_WATCHES: _delete_custom_sensor(
                        self._email_watches(), self._watch_id
                    )
                }
            )
        return self.async_show_form(
            step_id="delete_email_watch",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "name": str(self._watch_draft.get("name", "Email watch"))
            },
        )

    def _save_options(self, changes: dict[str, Any]) -> ConfigFlowResult:
        """Save changes without discarding unrelated account options."""
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **changes}
        )
