# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
"""Tests for the clean three-action surface and privacy defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from custom_components.email_ha import _register_services
from custom_components.email_ha.const import (
    DOMAIN,
    SERVICE_FIND_EMAILS,
    SERVICE_GET_EMAIL_CONTENTS,
    SERVICE_SEARCH_EMAILS,
)
from homeassistant.exceptions import ServiceValidationError


class _Services:
    def __init__(self) -> None:
        self.registered = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_register(self, domain: str, service: str, handler, **kwargs) -> None:
        self.registered[(domain, service)] = (handler, kwargs["schema"])


def _configured_hass(entries: int = 1):
    services = _Services()
    configured = {}
    entry_objects = {}
    for number in range(entries):
        entry = SimpleNamespace(
            entry_id=f"entry-{number}",
            domain=DOMAIN,
            data={"email": f"user{number}@example.com"},
        )
        configured[entry.entry_id] = SimpleNamespace(config_entry=entry)
        entry_objects[entry.entry_id] = entry
    hass = SimpleNamespace(
        data={DOMAIN: configured},
        services=services,
        config_entries=SimpleNamespace(async_get_entry=entry_objects.get),
    )
    _register_services(hass)
    return hass


def test_only_clean_public_actions_are_registered() -> None:
    """Legacy query/get names and aliases are absent."""
    hass = _configured_hass()

    assert {
        service for domain, service in hass.services.registered if domain == DOMAIN
    } == {
        SERVICE_FIND_EMAILS,
        SERVICE_SEARCH_EMAILS,
        SERVICE_GET_EMAIL_CONTENTS,
    }
    assert (DOMAIN, "query_emails") not in hass.services.registered
    assert (DOMAIN, "get_message") not in hass.services.registered


@pytest.mark.asyncio
async def test_find_emails_translates_filters_and_defaults_to_no_body(
    monkeypatch,
) -> None:
    """Friendly filters use safe tokens while metadata-only remains default."""
    hass = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "2"}])
    monkeypatch.setattr("custom_components.email_ha._search_structured", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_FIND_EMAILS)]

    response = await handler(
        SimpleNamespace(
            data=schema(
                {
                    "from": "rsa.ie",
                    "subject": "renewal",
                    "read_state": "unread",
                }
            )
        )
    )

    assert search.await_args.kwargs["tokens"] == [
        "FROM",
        '"rsa.ie"',
        "SUBJECT",
        '"renewal"',
        "UNSEEN",
    ]
    assert search.await_args.kwargs["include_body"] is False
    assert "plain_text_body" not in response["emails"][0]


@pytest.mark.asyncio
async def test_find_emails_body_opt_in_and_limit_are_propagated(monkeypatch) -> None:
    """Private content requires an explicit bounded action choice."""
    hass = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "2", "plain_text_body": "Body"}])
    monkeypatch.setattr("custom_components.email_ha._search_structured", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_FIND_EMAILS)]

    await handler(
        SimpleNamespace(data=schema({"include_body": True, "body_max_chars": 321}))
    )

    assert search.await_args.kwargs["include_body"] is True
    assert search.await_args.kwargs["body_max_chars"] == 321


@pytest.mark.asyncio
async def test_advanced_search_propagates_raw_query(monkeypatch) -> None:
    """Raw IMAP remains available through one clearly advanced action."""
    hass = _configured_hass()
    search = AsyncMock(return_value=[])
    monkeypatch.setattr("custom_components.email_ha._search_raw", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_SEARCH_EMAILS)]

    response = await handler(
        SimpleNamespace(data=schema({"search_criteria": "UNSEEN UID 4:*"}))
    )

    assert search.await_args.kwargs["criteria"] == "UNSEEN UID 4:*"
    assert response["search_criteria"] == "UNSEEN UID 4:*"


@pytest.mark.asyncio
async def test_get_email_contents_uses_selected_folder_uid_and_limit(
    monkeypatch,
) -> None:
    """Explicit retrieval has one clear name and propagates folder-specific input."""
    hass = _configured_hass()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get_email_contents.return_value = {
        "uid": "7",
        "plain_text_body": "Body",
    }
    monkeypatch.setattr(
        "custom_components.email_ha._connect_for_call",
        AsyncMock(return_value=client),
    )
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_GET_EMAIL_CONTENTS)]

    response = await handler(
        SimpleNamespace(
            data=schema({"folder": "Receipts", "uid": "7", "body_max_chars": 456})
        )
    )

    client.get_email_contents.assert_awaited_once_with(
        "Receipts", "7", body_max_chars=456
    )
    assert response["message"]["plain_text_body"] == "Body"


def test_action_schemas_reject_malformed_or_oversized_input() -> None:
    """UID format and body/result bounds are enforced before IMAP calls."""
    hass = _configured_hass()
    _, find_schema = hass.services.registered[(DOMAIN, SERVICE_FIND_EMAILS)]
    _, contents_schema = hass.services.registered[(DOMAIN, SERVICE_GET_EMAIL_CONTENTS)]

    with pytest.raises(vol.Invalid):
        find_schema({"max_results": 26})
    with pytest.raises(vol.Invalid):
        find_schema({"folder": "INBOX\r\nLOGOUT"})
    with pytest.raises(vol.Invalid):
        contents_schema({"uid": "1:*"})
    with pytest.raises(vol.Invalid):
        contents_schema({"uid": "1", "body_max_chars": 20001})


@pytest.mark.asyncio
async def test_multiple_accounts_require_explicit_selection(monkeypatch) -> None:
    """No arbitrary account is chosen when more than one is loaded."""
    hass = _configured_hass(entries=2)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_FIND_EMAILS)]
    monkeypatch.setattr(
        "custom_components.email_ha._search_structured", AsyncMock(return_value=[])
    )

    with pytest.raises(ServiceValidationError, match="Multiple Gmail accounts"):
        await handler(SimpleNamespace(data=schema({})))
