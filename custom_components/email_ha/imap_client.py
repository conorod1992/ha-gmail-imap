"""Async, read-only IMAP client with XOAUTH2 authentication."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import contextlib
import re
import shlex
from typing import Any, Self, cast

import aioimaplib
from aioimaplib.aioimaplib import STOP_WAIT_SERVER_PUSH

from .const import (
    IMAP_TIMEOUT,
    MAX_BODY_CHARS,
    MAX_HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_SEARCH_CRITERIA_CHARS,
    MAX_SEARCH_RESULTS,
    MAX_SEARCH_TOKENS,
)
from .mime_parser import parse_email_bytes
from .search import validate_imap_folder, validate_search_tokens

_FLAGS_RE = re.compile(r"FLAGS \(([^)]*)\)", re.IGNORECASE)
_INTERNAL_DATE_RE = re.compile(r'INTERNALDATE "([^"]+)"', re.IGNORECASE)


class ImapAuthError(Exception):
    """Raised when XOAUTH2 authentication fails."""


class ImapClientError(Exception):
    """Raised on a temporary IMAP or connection failure."""


class ImapFolderError(ImapClientError):
    """Raised when a folder cannot be selected."""


class ImapSearchError(ImapClientError):
    """Raised when IMAP search criteria are rejected."""


class ImapMessageNotFoundError(ImapClientError):
    """Raised when a folder-specific UID is not found."""


def tokenize_search_criteria(criteria: str) -> list[str]:
    """Validate and split IMAP criteria while preserving quoted values."""
    criteria = criteria.strip()
    if not criteria:
        raise ValueError("Search criteria must not be empty")
    if len(criteria) > MAX_SEARCH_CRITERIA_CHARS:
        raise ValueError("Search criteria are too long")
    if any(character in criteria for character in ("\r", "\n", "\x00")):
        raise ValueError("Search criteria contain invalid control characters")

    lexer = shlex.shlex(criteria, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as err:
        raise ValueError("Search criteria contain an unmatched quote") from err
    if not tokens or len(tokens) > MAX_SEARCH_TOKENS:
        raise ValueError("Search criteria are too complex")
    return tokens


def _extract_literal_bytes(lines: list[Any]) -> bytes | None:
    """Return the largest literal byte block from an aioimaplib response."""
    candidates = [bytes(item) for item in lines if isinstance(item, (bytes, bytearray))]
    literals = [item for item in candidates if b"\n" in item and b":" in item]
    return max(literals or candidates, key=len, default=None)


def _fetch_metadata(lines: list[Any]) -> tuple[list[str], str | None]:
    """Extract non-sensitive IMAP metadata from FETCH status lines."""
    status = " ".join(
        item.decode(errors="replace") if isinstance(item, bytes) else str(item)
        for item in lines
        if isinstance(item, (bytes, str)) and len(item) < 2048
    )
    flags_match = _FLAGS_RE.search(status)
    date_match = _INTERNAL_DATE_RE.search(status)
    flags = flags_match.group(1).split() if flags_match else []
    return flags, date_match.group(1) if date_match else None


class ImapClient:
    """Short-lived async IMAP client that only exposes read operations."""

    def __init__(self, host: str, port: int = 993) -> None:
        self._host = host
        self._port = port
        self._client: aioimaplib.IMAP4_SSL | None = None

    async def __aenter__(self) -> Self:
        """Enter the connection context."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Disconnect on context exit."""
        await self.disconnect()

    async def connect(self, user: str, access_token: str) -> None:
        """Open TLS and authenticate with XOAUTH2 without logging secrets."""
        try:
            client = aioimaplib.IMAP4_SSL(
                host=self._host, port=self._port, timeout=IMAP_TIMEOUT
            )
            async with asyncio.timeout(IMAP_TIMEOUT):
                await client.wait_hello_from_server()
                response = await client.xoauth2(user, cast(bytes, access_token))
        except (TimeoutError, OSError, aioimaplib.AioImapException) as err:
            raise ImapClientError("Unable to connect to the IMAP server") from err
        if response.result != "OK":
            with contextlib.suppress(OSError, aioimaplib.AioImapException):
                await client.logout()
            raise ImapAuthError("Gmail rejected IMAP authentication")
        self._client = client

    async def disconnect(self) -> None:
        """Log out and release the connection."""
        if self._client is None:
            return
        try:
            async with asyncio.timeout(10):
                await self._client.logout()
        except (TimeoutError, OSError, aioimaplib.AioImapException):
            pass
        finally:
            self._client = None

    async def list_folders(self) -> list[str]:
        """Return selectable folder names."""
        client = self._require_client()
        response = await client.list('""', cast(re.Pattern[str], '"*"'))
        if response.result != "OK":
            raise ImapClientError("Unable to list IMAP folders")
        folders: list[str] = []
        for line in response.lines:
            text = (
                line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            )
            if r"\Noselect" in text:
                continue
            match = re.search(r'"/" (?:"([^"]+)"|(\S+))$', text)
            if match:
                folders.append(match.group(1) or match.group(2))
        return folders

    async def get_folder_status(self, folder: str) -> dict[str, int]:
        """Return message and unseen counts for a folder."""
        folder = validate_imap_folder(folder)
        response = await self._require_client().status(
            folder, "(MESSAGES UNSEEN UIDVALIDITY UIDNEXT)"
        )
        if response.result != "OK":
            raise ImapFolderError("The selected folder is not accessible")
        result = {"messages": 0, "unseen": 0}
        for line in response.lines:
            text = (
                line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            )
            for key in ("MESSAGES", "UNSEEN", "UIDVALIDITY", "UIDNEXT"):
                if match := re.search(rf"{key} (\d+)", text):
                    result[key.lower()] = int(match.group(1))
        return result

    async def search_emails(
        self,
        folder: str,
        criteria: str = "ALL",
        max_results: int = 10,
        *,
        include_body: bool = False,
        body_max_chars: int = 4000,
    ) -> list[dict[str, Any]]:
        """Search one folder and return newest matching messages first."""
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        if not 1 <= body_max_chars <= MAX_BODY_CHARS:
            raise ValueError(f"body_max_chars must be between 1 and {MAX_BODY_CHARS}")
        tokens = tokenize_search_criteria(criteria)
        return await self.search_emails_tokens(
            folder,
            tokens,
            max_results,
            include_body=include_body,
            body_max_chars=body_max_chars,
        )

    async def search_emails_tokens(
        self,
        folder: str,
        tokens: Sequence[str],
        max_results: int = 10,
        *,
        include_body: bool = False,
        body_max_chars: int = 4000,
    ) -> list[dict[str, Any]]:
        """Search with pre-tokenized criteria and return newest matches first."""
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        if not 1 <= body_max_chars <= MAX_BODY_CHARS:
            raise ValueError(f"body_max_chars must be between 1 and {MAX_BODY_CHARS}")
        uids = await self._search_uids(folder, validate_search_tokens(tokens))
        return [
            message
            for uid in reversed(uids[-max_results:])
            if (
                message := await self._fetch_email(
                    uid,
                    folder,
                    include_body=include_body,
                    body_max_chars=body_max_chars,
                )
            )
            is not None
        ]

    async def count_emails(
        self, folder: str, tokens: Sequence[str]
    ) -> tuple[int, str | None]:
        """Return the matching UID count and newest UID without fetching messages."""
        uids = await self._search_uids(folder, validate_search_tokens(tokens))
        return len(uids), uids[-1] if uids else None

    async def get_new_emails(
        self, folder: str, after_uid: int, max_results: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Return bounded headers for UIDs newer than a known monotonic baseline."""
        if after_uid < 0:
            raise ValueError("after_uid must not be negative")
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        uids = await self._search_uids(folder, ["UID", f"{after_uid + 1}:*"])
        newer_uids = [uid for uid in uids if uid.isdecimal() and int(uid) > after_uid]
        selected_uids = newer_uids[-max_results:]
        messages = [
            message
            for uid in selected_uids
            if (
                message := await self._fetch_email(
                    uid, folder, include_body=False, body_max_chars=1
                )
            )
            is not None
        ]
        return messages, len(newer_uids)

    async def _search_uids(self, folder: str, tokens: Sequence[str]) -> list[str]:
        """Run a read-only UID SEARCH and return matching UIDs."""
        client = self._require_client()
        await self._select_read_only(folder)
        response = await client.uid_search(*tokens, charset=None)
        if response.result != "OK":
            raise ImapSearchError("The IMAP server rejected the search criteria")
        if not response.lines:
            return []
        uid_line = response.lines[0]
        uid_text = (
            uid_line.decode(errors="replace")
            if isinstance(uid_line, bytes)
            else str(uid_line)
        )
        return uid_text.strip().split()

    async def get_email_contents(
        self, folder: str, uid: str, *, body_max_chars: int
    ) -> dict[str, Any]:
        """Return one message by its folder-specific UID."""
        if not uid.isdecimal():
            raise ValueError("UID must contain digits only")
        if not 1 <= body_max_chars <= MAX_BODY_CHARS:
            raise ValueError(f"body_max_chars must be between 1 and {MAX_BODY_CHARS}")
        await self._select_read_only(folder)
        message = await self._fetch_email(
            uid, folder, include_body=True, body_max_chars=body_max_chars
        )
        if message is None:
            raise ImapMessageNotFoundError(
                "No message with that UID exists in the selected folder"
            )
        return message

    async def _select_read_only(self, folder: str) -> None:
        folder = validate_imap_folder(folder)
        client = self._require_client()
        response = await client.examine(folder)
        if response.result != "OK":
            raise ImapFolderError("The selected folder is not accessible")
        # aioimaplib 2.0.1 sends EXAMINE but does not update its local state,
        # which would incorrectly reject the following UID FETCH/SEARCH.
        if client.protocol is not None:
            client.protocol.state = "SELECTED"

    async def select_folder_read_only(self, folder: str) -> None:
        """Select a folder read-only before entering IMAP IDLE."""
        await self._select_read_only(folder)

    async def _fetch_email(
        self,
        uid: str,
        folder: str,
        *,
        include_body: bool,
        body_max_chars: int,
    ) -> dict[str, Any] | None:
        client = self._require_client()
        section = (
            f"BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>"
            if include_body
            else f"BODY.PEEK[HEADER]<0.{MAX_HEADER_BYTES}>"
        )
        response = await client.uid("fetch", uid, f"({section} FLAGS INTERNALDATE)")
        if response.result != "OK":
            return None
        raw = _extract_literal_bytes(response.lines)
        if not raw or b"\n" not in raw:
            return None
        flags, internal_date = _fetch_metadata(response.lines)
        return await asyncio.to_thread(
            parse_email_bytes,
            raw,
            uid,
            folder,
            include_body=include_body,
            body_max_chars=body_max_chars,
            flags=flags,
            internal_date=internal_date,
        )

    def _require_client(self) -> aioimaplib.IMAP4_SSL:
        if self._client is None:
            raise ImapClientError("Not connected")
        return self._client

    async def idle_wait(self, timeout: float) -> list[bytes] | None:
        """Run one IDLE cycle; a folder must already be selected."""
        client = self._require_client()
        idle = await client.idle_start(timeout=timeout)
        try:
            push = cast(list[bytes], await client.wait_server_push())
        except asyncio.TimeoutError:
            return None
        else:
            if push is STOP_WAIT_SERVER_PUSH or not push:
                return None
            return [line for line in push if isinstance(line, bytes)] or None
        finally:
            client.idle_done()
            if not idle.done():
                idle.cancel()
            with contextlib.suppress(
                asyncio.CancelledError, aioimaplib.AioImapException
            ):
                async with asyncio.timeout(10):
                    await idle
