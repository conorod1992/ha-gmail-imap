"""Tests for non-blocking IMAP TLS setup."""

from __future__ import annotations

import ssl
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.email_ha.const import IMAP_TIMEOUT
from custom_components.email_ha.imap_client import ImapClient


@pytest.mark.asyncio
async def test_connect_creates_ssl_context_off_event_loop() -> None:
    """Certificate loading runs in a worker thread and the context is reused by IMAP."""
    event_loop_thread = threading.get_ident()
    context_threads: list[int] = []
    ssl_context = object()

    def create_default_context(purpose: ssl.Purpose) -> object:
        context_threads.append(threading.get_ident())
        assert purpose is ssl.Purpose.SERVER_AUTH
        return ssl_context

    protocol = MagicMock()
    protocol.wait_hello_from_server = AsyncMock()
    protocol.xoauth2 = AsyncMock(return_value=SimpleNamespace(result="OK"))

    with (
        patch(
            "custom_components.email_ha.imap_client.ssl.create_default_context",
            side_effect=create_default_context,
        ) as create_context,
        patch(
            "custom_components.email_ha.imap_client.aioimaplib.IMAP4_SSL",
            return_value=protocol,
        ) as imap_ssl,
    ):
        client = ImapClient("imap.gmail.com")
        await client.connect("person@example.com", "access-token")

    assert context_threads
    assert context_threads[0] != event_loop_thread
    create_context.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
    imap_ssl.assert_called_once_with(
        host="imap.gmail.com",
        port=993,
        timeout=IMAP_TIMEOUT,
        ssl_context=ssl_context,
    )
    assert client._client is protocol  # noqa: SLF001
