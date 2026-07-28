"""Read-only Gmail access for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.config_entry_oauth2_flow import (
    ImplementationUnavailableError,
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CUSTOM_SENSORS,
    CONF_EMAIL,
    CONF_MONITORED_FOLDER,
    DEFAULT_FOLDER,
    DEFAULT_MESSAGE_BODY_CHARS,
    DEFAULT_SEARCH_BODY_CHARS,
    DEFAULT_SEARCH_RESULTS,
    DOMAIN,
    GMAIL_IMAP_HOST,
    GMAIL_IMAP_PORT,
    MAX_BODY_CHARS,
    MAX_SEARCH_RESULTS,
    PLATFORMS,
    SERVICE_ATTR_FOLDER,
    SERVICE_ATTR_MAX_RESULTS,
    SERVICE_ATTR_SEARCH_CRITERIA,
    SERVICE_FIND_EMAILS,
    SERVICE_GET_EMAIL_CONTENTS,
    SERVICE_SEARCH_EMAILS,
)
from .coordinator import EmailDataUpdateCoordinator
from .gmail import enabled_entities_for_entry
from .imap_client import (
    ImapAuthError,
    ImapClient,
    ImapClientError,
    ImapFolderError,
    ImapMessageNotFoundError,
    ImapSearchError,
    tokenize_search_criteria,
)
from .search import (
    GMAIL_CATEGORIES,
    IMPORTANT_STATES,
    READ_STATES,
    STARRED_STATES,
    build_structured_search_tokens,
    normalize_structured_filters,
    validate_imap_folder,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_ACCOUNT_FIELD = {vol.Optional("config_entry_id"): cv.string}
SEARCH_EMAILS_SCHEMA = vol.Schema(
    {
        **_ACCOUNT_FIELD,
        vol.Optional(SERVICE_ATTR_FOLDER, default=DEFAULT_FOLDER): vol.All(
            cv.string, validate_imap_folder
        ),
        vol.Optional(SERVICE_ATTR_SEARCH_CRITERIA, default="ALL"): cv.string,
        vol.Optional(SERVICE_ATTR_MAX_RESULTS, default=DEFAULT_SEARCH_RESULTS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_SEARCH_RESULTS)
        ),
        vol.Optional("include_body", default=False): cv.boolean,
        vol.Optional("body_max_chars", default=DEFAULT_SEARCH_BODY_CHARS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_BODY_CHARS)
        ),
    }
)
FIND_EMAILS_SCHEMA = vol.Schema(
    {
        **_ACCOUNT_FIELD,
        vol.Optional(SERVICE_ATTR_FOLDER, default=DEFAULT_FOLDER): vol.All(
            cv.string, validate_imap_folder
        ),
        vol.Optional("from"): cv.string,
        vol.Optional("to"): cv.string,
        vol.Optional("cc"): cv.string,
        vol.Optional("subject"): cv.string,
        vol.Optional("body"): cv.string,
        vol.Optional("text"): cv.string,
        vol.Optional("read_state", default="any"): vol.In(READ_STATES),
        vol.Optional("starred_state", default="any"): vol.In(STARRED_STATES),
        vol.Optional("important_state", default="any"): vol.In(IMPORTANT_STATES),
        vol.Optional("gmail_category", default="any"): vol.In(
            ("any", *GMAIL_CATEGORIES)
        ),
        vol.Optional("since"): cv.string,
        vol.Optional("before"): cv.string,
        vol.Optional("on"): cv.string,
        vol.Optional(SERVICE_ATTR_MAX_RESULTS, default=DEFAULT_SEARCH_RESULTS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_SEARCH_RESULTS)
        ),
        vol.Optional("include_body", default=False): cv.boolean,
        vol.Optional("body_max_chars", default=DEFAULT_SEARCH_BODY_CHARS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_BODY_CHARS)
        ),
    }
)
GET_EMAIL_CONTENTS_SCHEMA = vol.Schema(
    {
        **_ACCOUNT_FIELD,
        vol.Optional(SERVICE_ATTR_FOLDER, default=DEFAULT_FOLDER): vol.All(
            cv.string, validate_imap_folder
        ),
        vol.Required("uid"): vol.All(cv.string, vol.Match(r"^[0-9]+$")),
        vol.Optional("body_max_chars", default=DEFAULT_MESSAGE_BODY_CHARS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_BODY_CHARS)
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the three read-only response actions."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Gmail account."""
    try:
        implementation = await async_get_config_entry_implementation(hass, entry)
    except ImplementationUnavailableError as err:
        raise ConfigEntryNotReady(
            "OAuth2 implementation temporarily unavailable; will retry"
        ) from err
    coordinator = EmailDataUpdateCoordinator(
        hass=hass,
        config_entry=entry,
        oauth_session=OAuth2Session(hass, entry, implementation),
        email_address=entry.data[CONF_EMAIL],
        imap_host=GMAIL_IMAP_HOST,
        imap_port=GMAIL_IMAP_PORT,
        folder=entry.options.get(CONF_MONITORED_FOLDER, DEFAULT_FOLDER),
        enabled_gmail_entities=enabled_entities_for_entry(entry),
        custom_sensors=entry.options.get(CONF_CUSTOM_SENSORS, []),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise ConfigEntryNotReady("Initial Gmail fetch failed; will retry") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    coordinator.start_idle()
    entry.async_on_unload(coordinator.stop_idle)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities and release the account coordinator."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after entity, custom-sensor, or folder management changes."""
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> EmailDataUpdateCoordinator:
    configured: dict[str, EmailDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    entry_id: str | None = call.data.get("config_entry_id")
    if entry_id is None:
        if len(configured) == 1:
            return next(iter(configured.values()))
        if not configured:
            raise ServiceValidationError("No loaded Email HA account is available")
        raise ServiceValidationError(
            "Multiple Gmail accounts are configured; select an account"
        )
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            "The selected config entry is not an Email HA account"
        )
    if coordinator := configured.get(entry_id):
        return coordinator
    raise ServiceValidationError("The selected Email HA account is not loaded")


def _entry_for_coordinator(coordinator: EmailDataUpdateCoordinator) -> ConfigEntry:
    if coordinator.config_entry is None:
        raise HomeAssistantError("The selected Email HA account is not loaded")
    return coordinator.config_entry


async def _connect_for_call(coordinator: EmailDataUpdateCoordinator) -> ImapClient:
    """Open one short-lived read-only connection for an explicit action."""
    try:
        await coordinator.oauth_session.async_ensure_token_valid()
        token: dict[str, Any] = coordinator.oauth_session.token
        access_token = token["access_token"]
        if isinstance(access_token, bytes):
            access_token = access_token.decode()
        client = ImapClient(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        await client.connect(
            _entry_for_coordinator(coordinator).data[CONF_EMAIL],
            str(access_token),
        )
    except ImapAuthError as err:
        raise HomeAssistantError(
            "Gmail rejected authentication; reauthenticate the integration"
        ) from err
    except ImapClientError as err:
        raise HomeAssistantError("Unable to connect to Gmail IMAP") from err
    except Exception as err:
        raise HomeAssistantError(
            "OAuth authentication failed; reauthenticate the integration"
        ) from err
    return client


def _translate_imap_error(err: ImapClientError) -> HomeAssistantError:
    if isinstance(err, ImapFolderError):
        return HomeAssistantError("The selected IMAP folder is not accessible")
    if isinstance(err, ImapSearchError):
        return HomeAssistantError("Gmail rejected the IMAP search criteria")
    if isinstance(err, ImapMessageNotFoundError):
        return HomeAssistantError(
            "No email with that UID exists in the selected folder"
        )
    return HomeAssistantError("The Gmail IMAP operation failed")


async def _search_raw(
    coordinator: EmailDataUpdateCoordinator,
    *,
    folder: str,
    criteria: str,
    max_results: int,
    include_body: bool,
    body_max_chars: int,
) -> list[dict[str, Any]]:
    try:
        tokenize_search_criteria(criteria)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    client = await _connect_for_call(coordinator)
    try:
        async with client:
            return await client.search_emails(
                folder,
                criteria,
                max_results,
                include_body=include_body,
                body_max_chars=body_max_chars,
            )
    except ImapClientError as err:
        raise _translate_imap_error(err) from err


async def _search_structured(
    coordinator: EmailDataUpdateCoordinator,
    *,
    folder: str,
    tokens: list[str],
    max_results: int,
    include_body: bool,
    body_max_chars: int,
) -> list[dict[str, Any]]:
    client = await _connect_for_call(coordinator)
    try:
        async with client:
            return await client.search_emails_tokens(
                folder,
                tokens,
                max_results,
                include_body=include_body,
                body_max_chars=body_max_chars,
            )
    except ImapClientError as err:
        raise _translate_imap_error(err) from err


def _register_services(hass: HomeAssistant) -> None:
    """Register one normal search, one advanced search, and explicit retrieval."""
    if hass.services.has_service(DOMAIN, SERVICE_FIND_EMAILS):
        return

    async def handle_find_emails(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator_for_call(hass, call)
        folder = call.data[SERVICE_ATTR_FOLDER]
        try:
            filters = normalize_structured_filters(call.data)
            tokens = build_structured_search_tokens(filters)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        emails = await _search_structured(
            coordinator,
            folder=folder,
            tokens=tokens,
            max_results=call.data[SERVICE_ATTR_MAX_RESULTS],
            include_body=call.data["include_body"],
            body_max_chars=call.data["body_max_chars"],
        )
        return {
            "account": _entry_for_coordinator(coordinator).data[CONF_EMAIL],
            "folder": folder,
            "filters": filters,
            "count": len(emails),
            "emails": emails,
            "truncated": len(emails) == call.data[SERVICE_ATTR_MAX_RESULTS],
        }

    async def handle_search_emails(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator_for_call(hass, call)
        folder = call.data[SERVICE_ATTR_FOLDER]
        criteria = call.data[SERVICE_ATTR_SEARCH_CRITERIA]
        emails = await _search_raw(
            coordinator,
            folder=folder,
            criteria=criteria,
            max_results=call.data[SERVICE_ATTR_MAX_RESULTS],
            include_body=call.data["include_body"],
            body_max_chars=call.data["body_max_chars"],
        )
        return {
            "account": _entry_for_coordinator(coordinator).data[CONF_EMAIL],
            "folder": folder,
            "search_criteria": criteria,
            "count": len(emails),
            "emails": emails,
            "truncated": len(emails) == call.data[SERVICE_ATTR_MAX_RESULTS],
        }

    async def handle_get_email_contents(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator_for_call(hass, call)
        folder = call.data[SERVICE_ATTR_FOLDER]
        client = await _connect_for_call(coordinator)
        try:
            async with client:
                message = await client.get_email_contents(
                    folder,
                    call.data["uid"],
                    body_max_chars=call.data["body_max_chars"],
                )
        except ImapClientError as err:
            raise _translate_imap_error(err) from err
        return {
            "account": _entry_for_coordinator(coordinator).data[CONF_EMAIL],
            "folder": folder,
            "message": message,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_FIND_EMAILS,
        handle_find_emails,
        schema=FIND_EMAILS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_EMAILS,
        handle_search_emails,
        schema=SEARCH_EMAILS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_EMAIL_CONTENTS,
        handle_get_email_contents,
        schema=GET_EMAIL_CONTENTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
