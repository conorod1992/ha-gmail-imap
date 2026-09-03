"""Tests for read-only IMAP folder selection caching."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.email_ha.imap_client import ImapClient, ImapFolderError


def _client() -> tuple[ImapClient, AsyncMock]:
    wrapper = ImapClient("imap.example.com")
    client = AsyncMock()
    client.protocol = SimpleNamespace(state="AUTH")
    client.examine.return_value = SimpleNamespace(result="OK")
    wrapper._client = client  # noqa: SLF001
    return wrapper, client


@pytest.mark.asyncio
async def test_repeated_selection_of_same_folder_uses_one_examine() -> None:
    """Repeated operations in one mailbox do not re-EXAMINE it."""
    wrapper, client = _client()

    await wrapper._select_read_only("INBOX")  # noqa: SLF001
    await wrapper._select_read_only("INBOX")  # noqa: SLF001

    client.examine.assert_awaited_once_with("INBOX")
    assert client.protocol.state == "SELECTED"


@pytest.mark.asyncio
async def test_switching_folder_performs_new_examine() -> None:
    """Changing mailboxes still selects the requested folder explicitly."""
    wrapper, client = _client()

    await wrapper._select_read_only("INBOX")  # noqa: SLF001
    await wrapper._select_read_only("Receipts")  # noqa: SLF001

    assert client.examine.await_args_list[0].args == ("INBOX",)
    assert client.examine.await_args_list[1].args == ("Receipts",)
    assert client.examine.await_count == 2


@pytest.mark.asyncio
async def test_failed_selection_is_not_cached() -> None:
    """A failed EXAMINE is retried rather than poisoning selection state."""
    wrapper, client = _client()
    client.examine.side_effect = [
        SimpleNamespace(result="NO"),
        SimpleNamespace(result="OK"),
    ]

    with pytest.raises(ImapFolderError):
        await wrapper._select_read_only("Receipts")  # noqa: SLF001
    await wrapper._select_read_only("Receipts")  # noqa: SLF001

    assert client.examine.await_count == 2


@pytest.mark.asyncio
async def test_disconnect_clears_selected_folder() -> None:
    """A reconnect cannot inherit mailbox state from the old IMAP session."""
    wrapper, client = _client()
    await wrapper._select_read_only("INBOX")  # noqa: SLF001

    await wrapper.disconnect()

    assert wrapper._selected_folder is None  # noqa: SLF001
    client.logout.assert_awaited_once()
