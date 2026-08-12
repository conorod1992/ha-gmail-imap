"""Bounded, read-only MIME parsing helpers for Email HA."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
import re
from typing import Any

MAX_MIME_DEPTH = 20
MAX_MIME_PARTS = 200
PREVIEW_CHARS = 500
_MESSAGE_ID_RE = re.compile(r"<[^<>\r\n]{1,998}>")


@dataclass
class _PartBudget:
    """Track MIME traversal limits."""

    count: int = 0


class _HTMLTextExtractor(HTMLParser):
    """Extract readable text without loading or interpreting external content."""

    _BLOCK_TAGS = {
        "address",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "pre",
        "table",
        "tr",
    }
    _IGNORED_TAGS = {"head", "script", "style", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle an opening tag."""
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Handle a closing tag."""
        if tag in self._IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect visible text."""
        if not self._ignored_depth:
            self._text.append(data)

    def text(self) -> str:
        """Return normalized readable text."""
        joined = "".join(self._text).replace("\r\n", "\n").replace("\r", "\n")
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r" *\n *", "\n", joined)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def decode_header_value(value: str | None) -> str:
    """Decode RFC 2047 words using safe fallbacks."""
    if not value:
        return ""
    decoded: list[str] = []
    for part, charset in decode_header(value):
        if not isinstance(part, bytes):
            decoded.append(str(part))
            continue
        for encoding in (charset, "utf-8", "latin-1"):
            if not encoding:
                continue
            try:
                decoded.append(part.decode(encoding, errors="replace"))
                break
            except LookupError:
                continue
        else:
            decoded.append(part.decode("utf-8", errors="replace"))
    return "".join(decoded).strip()


def _addresses(message: Message, header: str) -> list[dict[str, str]]:
    values = message.get_all(header, [])
    return [
        {"name": decode_header_value(name), "address": address}
        for name, address in getaddresses(values)
        if name or address
    ]


def _message_ids(value: str | None) -> list[str]:
    return _MESSAGE_ID_RE.findall(value or "")


def _walk_parts(message: Message, budget: _PartBudget, depth: int = 0) -> list[Message]:
    if depth > MAX_MIME_DEPTH or budget.count >= MAX_MIME_PARTS:
        return []
    budget.count += 1
    if not message.is_multipart():
        return [message]
    result: list[Message] = []
    payload = message.get_payload()
    if isinstance(payload, list):
        for child in payload:
            if budget.count >= MAX_MIME_PARTS:
                break
            if isinstance(child, Message):
                result.extend(_walk_parts(child, budget, depth + 1))
    return result


def _decode_part(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except (LookupError, ValueError):
        return ""
    if not isinstance(payload, bytes):
        return str(payload or "")
    encodings = (part.get_content_charset(), "utf-8", "latin-1")
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return payload.decode(encoding, errors="replace")
        except LookupError:
            continue
    return payload.decode("utf-8", errors="replace")


def _body_and_attachments(message: Message) -> tuple[str, list[dict[str, Any]]]:
    plain: list[str] = []
    html: list[str] = []
    attachments: list[dict[str, Any]] = []
    for part in _walk_parts(message, _PartBudget()):
        disposition = part.get_content_disposition()
        filename = decode_header_value(part.get_filename())
        content_type = part.get_content_type()
        is_attachment = disposition == "attachment" or bool(filename)
        if is_attachment or (
            content_type.startswith("image/") and disposition == "inline"
        ):
            attachments.append(
                {
                    "filename": filename or None,
                    "content_type": content_type,
                    "content_disposition": disposition,
                    "content_id": part.get("Content-ID"),
                    "size": None,
                }
            )
            continue
        if content_type == "text/plain":
            plain.append(_decode_part(part))
        elif content_type == "text/html":
            html.append(_decode_part(part))

    body = "\n\n".join(text.strip() for text in plain if text.strip())
    if not body and html:
        extractor = _HTMLTextExtractor()
        try:
            extractor.feed("\n".join(html))
            extractor.close()
        except (AssertionError, ValueError):
            pass
        body = extractor.text()
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"[ \t]+\n", "\n", body)
    return re.sub(r"\n{4,}", "\n\n\n", body).strip(), attachments


def parse_email_bytes(
    raw: bytes,
    uid: str,
    folder: str,
    *,
    include_body: bool = False,
    body_max_chars: int = 4000,
    flags: list[str] | None = None,
    internal_date: str | None = None,
) -> dict[str, Any]:
    """Parse a message or header block into a serializable response."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    date_value: str | None = None
    if raw_date := message.get("Date"):
        try:
            parsed_date = parsedate_to_datetime(raw_date)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.astimezone()
            date_value = parsed_date.isoformat()
        except (IndexError, TypeError, ValueError, OverflowError):
            date_value = None

    senders = _addresses(message, "from")
    references = _message_ids(message.get("References"))
    in_reply_to_ids = _message_ids(message.get("In-Reply-To"))
    body, attachments = _body_and_attachments(message) if include_body else ("", [])
    truncated = len(body) > body_max_chars
    result: dict[str, Any] = {
        "uid": str(uid),
        "message_id": next(iter(_message_ids(message.get("Message-ID"))), None),
        "in_reply_to": next(iter(in_reply_to_ids), None),
        "references": references,
        "subject": decode_header_value(message.get("Subject")),
        "sender": senders[0] if senders else {"name": "", "address": ""},
        "to": _addresses(message, "to"),
        "cc": _addresses(message, "cc"),
        "reply_to": _addresses(message, "reply-to"),
        "date": date_value,
        "internal_date": internal_date,
        "flags": flags or [],
        "folder": folder,
        "preview": re.sub(r"\s+", " ", body)[:PREVIEW_CHARS],
        "has_attachments": bool(attachments),
        "attachment_count": len(attachments),
        "attachments": attachments,
    }
    if include_body:
        result["plain_text_body"] = body[:body_max_chars]
        result["body_truncated"] = truncated
    return result
