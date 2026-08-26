# pyright: reportArgumentType=false
"""Tests for privacy-conscious account diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator
from custom_components.email_ha.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redact_credentials_and_private_filters(monkeypatch) -> None:
    """Issue-report data contains operational counts, never secrets or rule text."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain="email_ha",
        data={
            "email": "private.user@example.com",
            "token": {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
            },
            "client_secret": "client-secret",
        },
        options={
            "monitored_folder": "INBOX",
            "custom_sensors": [
                {"id": "sensor-1", "filters": {"body": "private phrase"}}
            ],
            "email_watches": [
                {
                    "id": "watch-1",
                    "enabled": False,
                    "filters": {"from": "secret.example"},
                },
                {"id": "watch-2", "filters": {}},
            ],
        },
    )
    coordinator = SimpleNamespace(
        enabled_gmail_entities={"primary_unread"},
        last_success_time=datetime(2026, 8, 26, tzinfo=timezone.utc),
        idle_running=False,
        cached_folder_count=3,
        last_update_success=False,
        last_exception=RuntimeError("contains-private-details"),
        event_baseline_ready=True,
    )
    monkeypatch.setattr(
        "custom_components.email_ha.diagnostics.async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="2.2.0")),
    )
    monkeypatch.setattr(
        "custom_components.email_ha.diagnostics.coordinator_from_entry",
        Mock(return_value=coordinator),
    )

    result = await async_get_config_entry_diagnostics(SimpleNamespace(), entry)
    rendered = str(result)

    assert result["account"] == "p***@example.com"
    assert result["enabled_watch_count"] == 1
    assert result["disabled_watch_count"] == 1
    assert result["coordinator_last_exception"] == "RuntimeError"
    assert result["cached_folder_count"] == 3
    for secret in (
        "private.user",
        "access-secret",
        "refresh-secret",
        "client-secret",
        "private phrase",
        "secret.example",
        "contains-private-details",
    ):
        assert secret not in rendered


def test_completed_idle_task_is_not_running() -> None:
    """Diagnostics distinguish a finished task object from a live IDLE loop."""
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._idle_task = Mock()  # noqa: SLF001
    coordinator._idle_task.done.return_value = True  # noqa: SLF001

    assert coordinator.idle_running is False

    coordinator._idle_task.done.return_value = False  # noqa: SLF001
    assert coordinator.idle_running is True
