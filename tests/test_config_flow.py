# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
"""Tests for onboarding and state-based custom sensor management."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest
import voluptuous as vol

from custom_components.email_ha.config_flow import (
    EmailHAOptionsFlow,
    OAuth2FlowHandler,
    _custom_advanced_schema,
    _custom_common_schema,
    _custom_sensor_summary,
    _delete_custom_sensor,
    _folder_selector,
    _gmail_entities_schema,
    _ordered_gmail_entities,
    _upsert_custom_sensor,
)
from custom_components.email_ha.const import CONF_EMAIL, CONF_GMAIL_ENTITIES
from custom_components.email_ha.gmail import DEFAULT_GMAIL_ENTITIES
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.helpers.entity_registry import RegistryEntryDisabler


@pytest.mark.asyncio
async def test_setup_aborts_with_clear_missing_credentials(monkeypatch) -> None:
    """A user is directed to Application credentials before entering an account."""
    flow = OAuth2FlowHandler()
    flow.hass = SimpleNamespace()
    flow.async_abort = Mock(
        return_value={"type": "abort", "reason": "missing_credentials"}
    )
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.config_entry_oauth2_flow.async_get_implementations",
        AsyncMock(return_value={}),
    )

    result = await flow.async_step_user()

    assert cast(dict[str, Any], result)["reason"] == "missing_credentials"


@pytest.mark.asyncio
async def test_account_input_is_normalized_and_duplicate_checked() -> None:
    """Each Gmail address becomes an account-scoped unique config entry."""
    flow = OAuth2FlowHandler()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()  # noqa: SLF001
    flow.async_step_pick_implementation = AsyncMock(return_value={"type": "external"})

    result = await flow.async_step_user({CONF_EMAIL: " User@Example.COM "})

    assert result == {"type": "external"}
    flow.async_set_unique_id.assert_awaited_once_with("user@example.com")
    flow._abort_if_unique_id_configured.assert_called_once()  # noqa: SLF001


@pytest.mark.asyncio
async def test_oauth_continues_to_recommended_sensor_selection() -> None:
    """Successful OAuth does not expose mailbox implementation settings."""
    flow = OAuth2FlowHandler()
    flow.context = {"source": "user"}
    flow.async_step_sensors = AsyncMock(
        return_value={"type": "form", "step_id": "sensors"}
    )
    token_data = {"token": {"access_token": "secret"}}

    result = await flow.async_oauth_create_entry(token_data)

    assert cast(dict[str, Any], result)["step_id"] == "sensors"
    assert flow._token_data == token_data  # noqa: SLF001


@pytest.mark.asyncio
async def test_reauthentication_updates_token_without_replaying_setup() -> None:
    """Reauth keeps account options and updates only OAuth entry data."""
    flow = OAuth2FlowHandler()
    flow.context = {"source": SOURCE_REAUTH}
    entry = SimpleNamespace(entry_id="entry-1")
    flow._get_reauth_entry = Mock(return_value=entry)  # noqa: SLF001
    flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
    token_data = {"token": {"access_token": "renewed"}}

    result = await flow.async_oauth_create_entry(token_data)

    assert result == {"type": "abort"}
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry, data_updates=token_data, reason="reauth_successful"
    )


@pytest.mark.asyncio
async def test_sensor_selection_creates_clean_options() -> None:
    """Initial entity state is stored as options, not IMAP setup fields."""
    flow = OAuth2FlowHandler()
    flow._email = "user@example.com"  # noqa: SLF001
    flow._token_data = {"token": {"access_token": "secret"}}  # noqa: SLF001
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})

    await flow.async_step_sensors(
        {CONF_GMAIL_ENTITIES: ["new_email", "primary_unread"]}
    )

    kwargs = flow.async_create_entry.call_args.kwargs
    assert kwargs["data"][CONF_EMAIL] == "user@example.com"
    assert kwargs["options"] == {
        "gmail_entities": ["primary_unread", "new_email"],
        "monitored_folder": "INBOX",
    }
    assert "scan_interval" not in kwargs["data"]
    assert "folder" not in kwargs["data"]


@pytest.mark.asyncio
async def test_delete_removes_custom_entity_registry_entry(monkeypatch) -> None:
    """Deleting a custom object does not leave an orphan entity behind."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        options={
            "custom_sensors": [{"id": "custom-1", "name": "Bookings", "filters": {}}]
        },
    )
    config_entries = SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    flow = EmailHAOptionsFlow()
    flow.hass = SimpleNamespace(config_entries=config_entries)
    flow.handler = "entry-1"
    flow._custom_id = "custom-1"  # noqa: SLF001
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    registry = Mock()
    registry.async_get_entity_id.return_value = "sensor.gmail_bookings"
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.er.async_get",
        Mock(return_value=registry),
    )

    await flow.async_step_delete_custom_sensor({"confirm": True})

    registry.async_remove.assert_called_once_with("sensor.gmail_bookings")
    assert flow.async_create_entry.call_args.kwargs["data"]["custom_sensors"] == []


def test_gmail_sensor_state_reconciles_entity_registry(monkeypatch) -> None:
    """The single state screen enables and integration-disables fixed entities."""
    entry = SimpleNamespace(entry_id="entry-1", options={})
    flow = EmailHAOptionsFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    )
    flow.handler = "entry-1"
    registry = Mock()
    entries = [
        SimpleNamespace(
            unique_id="entry-1_primary_unread",
            entity_id="sensor.primary",
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        ),
        SimpleNamespace(
            unique_id="entry-1_inbox_unread",
            entity_id="sensor.inbox",
            disabled_by=None,
        ),
        SimpleNamespace(
            unique_id="entry-1_custom_private",
            entity_id="sensor.custom",
            disabled_by=None,
        ),
    ]
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.er.async_get",
        Mock(return_value=registry),
    )
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.er.async_entries_for_config_entry",
        Mock(return_value=entries),
    )

    flow._reconcile_entity_registry({"primary_unread"})  # noqa: SLF001

    assert registry.async_update_entity.call_args_list == [
        call("sensor.primary", disabled_by=None),
        call("sensor.inbox", disabled_by=RegistryEntryDisabler.INTEGRATION),
    ]


def test_onboarding_recommends_three_beginner_entities() -> None:
    """Primary unread, Latest email, and New email are preselected."""
    result = cast(
        dict[str, Any],
        _gmail_entities_schema(list(DEFAULT_GMAIL_ENTITIES))({}),
    )

    assert result["gmail_entities"] == [
        "primary_unread",
        "latest_email",
        "new_email",
    ]
    assert "inbox_unread" not in result["gmail_entities"]


def test_gmail_entity_selection_accepts_optional_entities() -> None:
    """Onboarding can enable useful optional Gmail concepts."""
    selected = ["primary_unread", "inbox_unread", "promotions_unread"]

    result = cast(
        dict[str, Any],
        _gmail_entities_schema(list(DEFAULT_GMAIL_ENTITIES))(
            {"gmail_entities": selected}
        ),
    )

    assert result["gmail_entities"] == selected


def test_gmail_entity_selection_rejects_unknown_key() -> None:
    """Only canonical fixed entities can enter stored state."""
    with pytest.raises(vol.Invalid):
        _gmail_entities_schema([])({"gmail_entities": ["old_unread_count"]})


def test_canonical_entity_order_is_stable() -> None:
    """State is stored in UI order regardless of submitted ordering."""
    assert _ordered_gmail_entities(
        {"mailbox_folders", "new_email", "primary_unread"}
    ) == ["primary_unread", "new_email", "mailbox_folders"]


def test_custom_common_form_is_approachable_and_body_free() -> None:
    """The first form shows common filters and safe defaults only."""
    result = cast(
        dict[str, Any],
        _custom_common_schema(["INBOX", "Receipts"], {})({"name": "RSA"}),
    )

    assert result["folder"] == "INBOX"
    assert result["read_state"] == "any"
    assert result["gmail_category"] == "any"
    assert result["more_filters"] is False
    assert "body" not in result
    assert "to" not in result


def test_custom_advanced_form_contains_progressively_disclosed_filters() -> None:
    """Recipient, body/text, and dates live in the second form."""
    schema = _custom_advanced_schema({"filters": {"body": "booking"}})
    result = cast(dict[str, Any], schema({"since": "2026-07-01"}))

    assert result["body"] == "booking"
    assert result["since"] == "2026-07-01"


def test_discovered_folder_selector_still_allows_arbitrary_folder() -> None:
    """Discovery improves the UI without restricting advanced folder names."""
    folder_selector = _folder_selector(["[Gmail]/All Mail", "Projects"])

    validate = cast(Any, folder_selector)
    assert validate("Projects") == "Projects"
    assert validate("Localised/Custom") == "Localised/Custom"


def test_custom_management_summary_identifies_private_filter() -> None:
    """The authenticated management UI can identify a sensor precisely."""
    summary = _custom_sensor_summary(
        {
            "name": "RSA unread",
            "folder": "INBOX",
            "filters": {"read_state": "unread", "from": "rsa.ie"},
        }
    )

    assert summary == 'RSA unread — Inbox · Unread · From contains "rsa.ie"'


def test_custom_sensor_add_edit_duplicate_delete_state() -> None:
    """Custom objects can be managed without delete-and-recreate editing."""
    original = {
        "name": "Bookings",
        "folder": "INBOX",
        "filters": {"subject": "booking"},
    }
    sensors = _upsert_custom_sensor([], original, sensor_id="one")
    edited = _upsert_custom_sensor(
        sensors,
        {**original, "filters": {"subject": "reservation"}},
        sensor_id="one",
        replace_id="one",
    )
    duplicated = _upsert_custom_sensor(
        edited, {**original, "name": "Copy of Bookings"}, sensor_id="two"
    )
    remaining = _delete_custom_sensor(duplicated, "one")

    assert edited[0]["id"] == "one"
    assert edited[0]["filters"]["subject"] == "reservation"
    assert [sensor["id"] for sensor in duplicated] == ["one", "two"]
    assert remaining == [
        {
            "name": "Copy of Bookings",
            "folder": "INBOX",
            "filters": {"subject": "booking"},
            "id": "two",
        }
    ]
