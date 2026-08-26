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
from custom_components.email_ha.imap_client import ImapClientError
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


@pytest.mark.asyncio
async def test_delete_email_watch_removes_event_registry_entry(monkeypatch) -> None:
    """Deleting a watch cleans up its UUID-backed EventEntity registry entry."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        options={"email_watches": [{"id": "watch-1", "name": "RSA"}]},
    )
    flow = EmailHAOptionsFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    )
    flow.handler = "entry-1"
    flow._watch_id = "watch-1"  # noqa: SLF001
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    registry = Mock()
    registry.async_get_entity_id.return_value = "event.gmail_rsa"
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.er.async_get",
        Mock(return_value=registry),
    )

    await flow.async_step_delete_email_watch({"confirm": True})

    registry.async_get_entity_id.assert_called_once_with(
        "event", "email_ha", "entry-1_watch_watch-1"
    )
    registry.async_remove.assert_called_once_with("event.gmail_rsa")
    assert flow.async_create_entry.call_args.kwargs["data"]["email_watches"] == []


def test_watch_edit_keeps_id_and_duplicate_gets_new_id(monkeypatch) -> None:
    """Watch names are mutable while persistent identity is not reused by copies."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        options={
            "email_watches": [
                {"id": "watch-1", "name": "RSA", "folder": "INBOX", "filters": {}}
            ]
        },
    )
    flow = EmailHAOptionsFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    )
    flow.handler = "entry-1"
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    flow._watch_mode = "edit"  # noqa: SLF001
    flow._watch_id = "watch-1"  # noqa: SLF001
    flow._watch_draft = {  # noqa: SLF001
        "id": "watch-1",
        "name": "Renamed RSA",
        "folder": "INBOX",
        "filters": {},
    }

    flow._finish_email_watch()  # noqa: SLF001

    edited = flow.async_create_entry.call_args.kwargs["data"]["email_watches"]
    assert edited[0]["id"] == "watch-1"
    assert edited[0]["name"] == "Renamed RSA"

    entry.options = {"email_watches": edited}
    flow._watch_mode = "duplicate"  # noqa: SLF001
    flow._watch_draft = {**edited[0], "name": "Copy of Renamed RSA"}  # noqa: SLF001
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.uuid4",
        Mock(return_value=SimpleNamespace(hex="watch-2")),
    )
    flow._finish_email_watch()  # noqa: SLF001

    duplicated = flow.async_create_entry.call_args.kwargs["data"]["email_watches"]
    assert [watch["id"] for watch in duplicated] == ["watch-1", "watch-2"]


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
    assert result["attachment_state"] == "any"
    assert result["more_filters"] is False
    assert "body" not in result
    assert "to" not in result


def test_custom_advanced_form_contains_progressively_disclosed_filters() -> None:
    """Recipient, body/text, and dates live in the second form."""
    schema = _custom_advanced_schema({"filters": {"body": "booking"}})
    result = cast(dict[str, Any], schema({"since": "2026-07-01"}))

    assert result["body"] == "booking"
    assert result["since"] == "2026-07-01"
    assert "attachment_filename" in {
        str(key.schema) for key in schema.schema if hasattr(key, "schema")
    }


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


def _options_flow(options: dict[str, Any]) -> tuple[EmailHAOptionsFlow, Any]:
    """Return a lightweight options flow bound to one in-memory entry."""
    entry = SimpleNamespace(entry_id="entry-1", options=options)
    flow = EmailHAOptionsFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    )
    flow.handler = "entry-1"
    flow.async_show_form = Mock(side_effect=lambda **kwargs: {"type": "form", **kwargs})
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})
    return flow, entry


@pytest.mark.asyncio
async def test_logical_watch_selection_leads_to_small_action_flow() -> None:
    """The item list contains one watch entry, then shows its own actions."""
    flow, _entry = _options_flow(
        {
            "email_watches": [
                {"id": "watch-1", "name": "RSA", "folder": "INBOX", "filters": {}}
            ]
        }
    )

    result = cast(
        dict[str, Any],
        await flow.async_step_email_watches({"manage_action": "manage:watch-1"}),
    )

    assert result["step_id"] == "email_watch_action"
    assert flow._watch_id == "watch-1"  # noqa: SLF001
    action_schema = result["data_schema"]
    assert action_schema({"action": "disable"})["action"] == "disable"


@pytest.mark.asyncio
async def test_logical_sensor_selection_leads_to_small_action_flow() -> None:
    """Custom sensors likewise expose one list item followed by three actions."""
    flow, _entry = _options_flow(
        {
            "custom_sensors": [
                {
                    "id": "sensor-1",
                    "name": "Bookings",
                    "folder": "INBOX",
                    "filters": {},
                }
            ]
        }
    )

    result = cast(
        dict[str, Any],
        await flow.async_step_custom_sensors({"manage_action": "manage:sensor-1"}),
    )

    assert result["step_id"] == "custom_sensor_action"
    assert flow._custom_id == "sensor-1"  # noqa: SLF001
    action_schema = result["data_schema"]
    assert action_schema({"action": "duplicate"})["action"] == "duplicate"


@pytest.mark.asyncio
async def test_enable_disable_action_retains_watch_id_and_legacy_defaults() -> None:
    """Missing enabled is treated as on and pausing only changes that state."""
    flow, _entry = _options_flow(
        {
            "email_watches": [
                {"id": "watch-1", "name": "RSA", "folder": "INBOX", "filters": {}}
            ]
        }
    )
    await flow.async_step_email_watches({"manage_action": "manage:watch-1"})

    await flow.async_step_email_watch_action({"action": "disable"})

    saved = flow.async_create_entry.call_args.kwargs["data"]["email_watches"]
    assert saved == [
        {
            "id": "watch-1",
            "name": "RSA",
            "folder": "INBOX",
            "filters": {},
            "enabled": False,
        }
    ]


@pytest.mark.asyncio
async def test_common_preview_is_bounded_body_free_and_does_not_persist(
    monkeypatch,
) -> None:
    """Testing a common-only draft searches five headers without saving state."""
    flow, _entry = _options_flow({"custom_sensors": []})
    coordinator = SimpleNamespace(
        async_preview_filter=AsyncMock(
            return_value=[
                {
                    "subject": "Match",
                    "sender": {"address": "sender@example.com"},
                    "date": "2026-08-26T10:00:00+00:00",
                    "body": "must not be rendered",
                }
            ]
        )
    )
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.coordinator_from_entry",
        Mock(return_value=coordinator),
    )
    flow._custom_mode = "add"  # noqa: SLF001

    result = cast(
        dict[str, Any],
        await flow.async_step_custom_sensor_common(
            {
                "name": "RSA",
                "folder": "INBOX",
                "from": "rsa.ie",
                "read_state": "any",
                "gmail_category": "any",
                "important_state": "any",
                "starred_state": "any",
                "attachment_state": "any",
                "more_filters": False,
                "test_filter": True,
            }
        ),
    )

    coordinator.async_preview_filter.assert_awaited_once_with(
        "INBOX", {"from": "rsa.ie"}, 5
    )
    assert result["step_id"] == "custom_sensor_preview"
    assert "up to 5" in result["description_placeholders"]["preview"]
    assert "must not be rendered" not in result["description_placeholders"]["preview"]
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_zero_match_preview_is_successful_and_preserves_baseline(
    monkeypatch,
) -> None:
    """Zero results are informative and testing never touches arrival state."""
    flow, _entry = _options_flow({"email_watches": []})
    coordinator = SimpleNamespace(
        async_preview_filter=AsyncMock(return_value=[]),
        _last_seen_uid=42,
        _folder_uid_state={"INBOX": (7, 42)},
    )
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.coordinator_from_entry",
        Mock(return_value=coordinator),
    )
    flow._watch_mode = "add"  # noqa: SLF001

    result = cast(
        dict[str, Any],
        await flow.async_step_email_watch_common(
            {
                "name": "No matches",
                "folder": "INBOX",
                "subject": "missing",
                "read_state": "any",
                "gmail_category": "any",
                "important_state": "any",
                "starred_state": "any",
                "attachment_state": "any",
                "more_filters": False,
                "test_filter": True,
                "enabled": True,
            }
        ),
    )

    assert "No matching emails" in result["description_placeholders"]["preview"]
    assert coordinator._last_seen_uid == 42  # noqa: SLF001
    assert coordinator._folder_uid_state == {"INBOX": (7, 42)}  # noqa: SLF001
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_preview_failure_keeps_draft_and_shows_clear_error(monkeypatch) -> None:
    """A Gmail failure returns a form error without discarding owner input."""
    flow, _entry = _options_flow({"custom_sensors": []})
    coordinator = SimpleNamespace(
        async_preview_filter=AsyncMock(side_effect=ImapClientError("offline"))
    )
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.coordinator_from_entry",
        Mock(return_value=coordinator),
    )
    flow._custom_mode = "add"  # noqa: SLF001

    result = cast(
        dict[str, Any],
        await flow.async_step_custom_sensor_common(
            {
                "name": "Retained",
                "folder": "INBOX",
                "from": "sender.example",
                "read_state": "any",
                "gmail_category": "any",
                "important_state": "any",
                "starred_state": "any",
                "attachment_state": "any",
                "more_filters": False,
                "test_filter": True,
            }
        ),
    )

    assert result["errors"] == {"base": "filter_test_failed"}
    assert flow._custom_draft["name"] == "Retained"  # noqa: SLF001
    assert flow._custom_draft["filters"] == {  # noqa: SLF001
        "from": "sender.example"
    }
    flow.async_create_entry.assert_not_called()


@pytest.mark.asyncio
async def test_edit_preview_save_retains_persistent_id(monkeypatch) -> None:
    """Testing an edited watch never turns it into a newly identified copy."""
    original = {
        "id": "watch-1",
        "name": "RSA",
        "folder": "INBOX",
        "filters": {"from": "rsa.ie"},
    }
    flow, _entry = _options_flow({"email_watches": [original]})
    coordinator = SimpleNamespace(async_preview_filter=AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "custom_components.email_ha.config_flow.coordinator_from_entry",
        Mock(return_value=coordinator),
    )
    flow._watch_mode = "edit"  # noqa: SLF001
    flow._watch_id = "watch-1"  # noqa: SLF001
    flow._watch_draft = dict(original)  # noqa: SLF001

    await flow.async_step_email_watch_preview()
    await flow.async_step_email_watch_preview({"save": True})

    saved = flow.async_create_entry.call_args.kwargs["data"]["email_watches"]
    assert len(saved) == 1
    assert saved[0]["id"] == "watch-1"


@pytest.mark.asyncio
async def test_advanced_preview_uses_complete_retained_draft() -> None:
    """Advanced fields are captured before preview and survive returning to edit."""
    flow, _entry = _options_flow({"custom_sensors": []})
    flow._custom_mode = "add"  # noqa: SLF001
    flow.async_step_custom_sensor_advanced = AsyncMock(
        return_value={"type": "form", "step_id": "custom_sensor_advanced"}
    )

    common = cast(
        dict[str, Any],
        await flow.async_step_custom_sensor_common(
            {
                "name": "RSA",
                "folder": "INBOX",
                "from": "rsa.ie",
                "read_state": "any",
                "gmail_category": "any",
                "important_state": "any",
                "starred_state": "any",
                "attachment_state": "any",
                "more_filters": True,
                "test_filter": True,
            }
        ),
    )

    assert common["step_id"] == "custom_sensor_advanced"
    flow.async_step_custom_sensor_preview = AsyncMock(
        return_value={"type": "form", "step_id": "custom_sensor_preview"}
    )
    preview = cast(
        dict[str, Any],
        await EmailHAOptionsFlow.async_step_custom_sensor_advanced(
            flow, {"body": "renewal", "since": "2026-01-01", "test_filter": True}
        ),
    )

    assert preview["step_id"] == "custom_sensor_preview"
    assert flow._custom_draft["filters"] == {  # noqa: SLF001
        "from": "rsa.ie",
        "body": "renewal",
        "since": "2026-01-01",
    }
    flow.async_step_custom_sensor_preview.assert_awaited_once()
