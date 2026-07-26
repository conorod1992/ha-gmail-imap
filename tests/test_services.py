# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
"""Tests for action schemas and include-body propagation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha import _register_services
from custom_components.email_ha.const import (
    DOMAIN,
    SERVICE_FIND_EMAILS,
    SERVICE_QUERY_EMAILS,
    SERVICE_SEARCH_EMAILS,
)


class _Services:
    """Minimal service registry exposing registered handlers to tests."""

    def __init__(self) -> None:
        self.registered = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_register(self, domain: str, service: str, handler, **kwargs) -> None:
        self.registered[(domain, service)] = (handler, kwargs["schema"])


def _configured_hass():
    entry = SimpleNamespace(
        entry_id="entry-1", domain=DOMAIN, data={"email": "user@example.com"}
    )
    coordinator = SimpleNamespace(config_entry=entry)
    services = _Services()
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        services=services,
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry),
    )
    _register_services(hass)
    return hass, coordinator


@pytest.mark.asyncio
async def test_raw_search_include_body_reaches_shared_helper(monkeypatch) -> None:
    """The existing raw action preserves its ID and propagates include_body."""
    hass, _ = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "1", "plain_text_body": "Body"}])
    monkeypatch.setattr("custom_components.email_ha._search", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_SEARCH_EMAILS)]

    response = await handler(
        SimpleNamespace(
            data=schema(
                {
                    "config_entry_id": "entry-1",
                    "search_criteria": "UNSEEN",
                    "include_body": True,
                    "body_max_chars": 321,
                }
            )
        )
    )

    assert response["emails"][0]["plain_text_body"] == "Body"
    assert search.await_args.kwargs["include_body"] is True
    assert search.await_args.kwargs["body_max_chars"] == 321


@pytest.mark.asyncio
async def test_raw_search_include_body_defaults_false(monkeypatch) -> None:
    """Raw search remains metadata-only unless body access is explicit."""
    hass, _ = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "1"}])
    monkeypatch.setattr("custom_components.email_ha._search", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_SEARCH_EMAILS)]

    response = await handler(
        SimpleNamespace(data=schema({"config_entry_id": "entry-1"}))
    )

    assert "plain_text_body" not in response["emails"][0]
    assert search.await_args.kwargs["include_body"] is False


@pytest.mark.asyncio
async def test_structured_search_translates_and_propagates_body(monkeypatch) -> None:
    """The friendly action passes safe tokens and its body controls unchanged."""
    hass, _ = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "2", "plain_text_body": "Body"}])
    monkeypatch.setattr("custom_components.email_ha._search_tokens", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_FIND_EMAILS)]

    response = await handler(
        SimpleNamespace(
            data=schema(
                {
                    "config_entry_id": "entry-1",
                    "from": "rsa.ie",
                    "subject": "renewal",
                    "read_state": "unread",
                    "include_body": True,
                    "body_max_chars": 222,
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
    assert search.await_args.kwargs["include_body"] is True
    assert search.await_args.kwargs["body_max_chars"] == 222
    assert response["filters"]["read_state"] == "unread"


@pytest.mark.asyncio
async def test_legacy_query_include_full_body_remains_compatible(monkeypatch) -> None:
    """The legacy field still controls the same shared body-fetch path."""
    hass, _ = _configured_hass()
    search = AsyncMock(return_value=[{"uid": "3", "body_text": "Body"}])
    monkeypatch.setattr("custom_components.email_ha._search", search)
    handler, schema = hass.services.registered[(DOMAIN, SERVICE_QUERY_EMAILS)]

    response = await handler(
        SimpleNamespace(
            data=schema({"config_entry_id": "entry-1", "include_full_body": True})
        )
    )

    assert response == {"emails": [{"uid": "3", "body_text": "Body"}]}
    assert search.await_args.kwargs["include_body"] is True
