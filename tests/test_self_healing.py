# pyright: reportAttributeAccessIssue=false
"""Regression tests for IDLE and persisted-state self-healing."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.email_ha.const import IDLE_RECONNECT_DELAYS
from custom_components.email_ha.coordinator import EmailDataUpdateCoordinator
from custom_components.email_ha.state import EmailStateStore


def _coordinator() -> EmailDataUpdateCoordinator:
    coordinator = object.__new__(EmailDataUpdateCoordinator)
    coordinator._email = "user@example.com"  # noqa: SLF001
    coordinator._folder = "INBOX"  # noqa: SLF001
    coordinator.custom_sensors = []
    coordinator.email_watches = []
    coordinator._folder_uid_state = {}  # noqa: SLF001
    coordinator._restored_folders = set()  # noqa: SLF001
    coordinator._watch_uid_state = {}  # noqa: SLF001
    coordinator._custom_last_new_match = {}  # noqa: SLF001
    coordinator._watch_last_new_match = {}  # noqa: SLF001
    coordinator._idle_task = None  # noqa: SLF001
    return coordinator


@pytest.mark.asyncio
async def test_load_state_prunes_deleted_rules_and_folders() -> None:
    """Obsolete persisted keys are dropped and the sanitized state is rewritten."""
    coordinator = _coordinator()
    coordinator.custom_sensors = [{"id": "sensor", "folder": "Receipts"}]
    coordinator.email_watches = [{"id": "watch", "folder": "Archive", "filters": {}}]
    state_store = SimpleNamespace(
        async_load=AsyncMock(),
        folder_uid_state={
            "INBOX": (1, 10),
            "Receipts": (2, 20),
            "Archive": (3, 30),
            "Deleted": (4, 40),
        },
        watch_uid_state={
            "watch": ("keep", 3, 30),
            "deleted-watch": ("remove", 4, 40),
        },
        has_watch_uid_state=True,
        custom_last_new_match={
            "sensor": "2026-09-01T00:00:00+00:00",
            "deleted-sensor": "2026-08-01T00:00:00+00:00",
        },
        watch_last_new_match={
            "watch": "2026-09-01T00:00:00+00:00",
            "deleted-watch": "2026-08-01T00:00:00+00:00",
        },
        async_schedule_save=Mock(),
    )
    coordinator._state_store = cast(Any, state_store)  # noqa: SLF001

    await coordinator.async_load_state()

    assert coordinator._folder_uid_state == {  # noqa: SLF001
        "INBOX": (1, 10),
        "Receipts": (2, 20),
        "Archive": (3, 30),
    }
    assert set(coordinator._restored_folders) == {  # noqa: SLF001
        "INBOX",
        "Receipts",
        "Archive",
    }
    assert coordinator._watch_uid_state == {"watch": ("keep", 3, 30)}  # noqa: SLF001
    assert coordinator._custom_last_new_match == {  # noqa: SLF001
        "sensor": "2026-09-01T00:00:00+00:00"
    }
    assert coordinator._watch_last_new_match == {  # noqa: SLF001
        "watch": "2026-09-01T00:00:00+00:00"
    }
    assert state_store.folder_uid_state == coordinator._folder_uid_state  # noqa: SLF001
    state_store.async_schedule_save.assert_called_once()


@pytest.mark.asyncio
async def test_state_store_repeated_load_does_not_keep_removed_values() -> None:
    """A second load starts clean instead of retaining values from the first load."""
    state_store = object.__new__(EmailStateStore)
    state_store._store = cast(  # noqa: SLF001
        Any,
        SimpleNamespace(
            async_load=AsyncMock(
                side_effect=[
                    {
                        "folders": {
                            "INBOX": {"uidvalidity": 1, "last_seen_uid": 10}
                        },
                        "watch_uid_state": {
                            "watch": {
                                "fingerprint": "abc",
                                "uidvalidity": 1,
                                "last_seen_uid": 10,
                            }
                        },
                        "custom_last_new_match": {"sensor": "timestamp"},
                        "watch_last_new_match": {"watch": "timestamp"},
                    },
                    {},
                ]
            )
        ),
    )

    await state_store.async_load()
    assert state_store.folder_uid_state == {"INBOX": (1, 10)}

    await state_store.async_load()

    assert state_store.folder_uid_state == {}
    assert state_store.watch_uid_state == {}
    assert state_store.has_watch_uid_state is False
    assert state_store.custom_last_new_match == {}
    assert state_store.watch_last_new_match == {}


@pytest.mark.asyncio
async def test_start_idle_replaces_a_finished_task(caplog) -> None:
    """A stale completed task reference cannot permanently block IDLE startup."""
    coordinator = _coordinator()
    finished = asyncio.create_task(asyncio.sleep(0))
    await finished
    coordinator._idle_task = finished  # noqa: SLF001
    replacement = asyncio.get_running_loop().create_future()

    def create_background_task(
        coro: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Future[None]:
        assert name == "email_ha:idle:user@example.com"
        coro.close()
        return replacement

    coordinator.hass = cast(  # type: ignore[assignment]
        Any,
        SimpleNamespace(async_create_background_task=Mock(side_effect=create_background_task)),
    )

    with caplog.at_level("WARNING"):
        coordinator.start_idle()

    assert coordinator._idle_task is replacement  # noqa: SLF001
    assert "stopped unexpectedly; restarting" in caplog.text
    replacement.cancel()


@pytest.mark.asyncio
async def test_stop_idle_does_not_rethrow_an_old_task_failure() -> None:
    """Unload remains clean when IDLE failed before shutdown began."""
    coordinator = _coordinator()

    async def fail() -> None:
        raise RuntimeError("boom")

    failed = asyncio.create_task(fail())
    await asyncio.sleep(0)
    assert failed.done()
    coordinator._idle_task = failed  # noqa: SLF001

    await coordinator.stop_idle()

    assert coordinator._idle_task is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_unexpected_idle_error_uses_bounded_reconnect_backoff(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Unexpected runtime failures are logged and retried instead of killing IDLE."""
    coordinator = _coordinator()
    run_session = AsyncMock(side_effect=RuntimeError("boom"))
    cast(Any, coordinator)._async_run_idle_session = run_session
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.email_ha.coordinator.asyncio.sleep", sleep)

    with caplog.at_level("ERROR"):
        reconnect_attempt = await coordinator._async_idle_attempt(0)  # noqa: SLF001

    assert reconnect_attempt == 1
    sleep.assert_awaited_once_with(IDLE_RECONNECT_DELAYS[0])
    assert "Unexpected IDLE error" in caplog.text


@pytest.mark.asyncio
async def test_idle_auth_failure_remains_terminal() -> None:
    """Self-healing must not turn a genuine reauth requirement into a retry loop."""
    coordinator = _coordinator()
    cast(Any, coordinator)._async_run_idle_session = AsyncMock(
        side_effect=ConfigEntryAuthFailed("reauth")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_idle_attempt(0)  # noqa: SLF001
