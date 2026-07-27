"""Safe structured IMAP search helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from .const import MAX_SEARCH_CRITERIA_CHARS, MAX_SEARCH_TOKENS

_TEXT_FILTERS = {
    "from": "FROM",
    "to": "TO",
    "cc": "CC",
    "subject": "SUBJECT",
    "body": "BODY",
    "text": "TEXT",
}
_DATE_FILTERS = {"since": "SINCE", "before": "BEFORE", "on": "ON"}
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
GMAIL_CATEGORIES = ("primary", "promotions", "social", "updates", "forums")
READ_STATES = ("any", "unread", "read")
STARRED_STATES = ("any", "starred", "not_starred")
IMPORTANT_STATES = ("any", "important", "not_important")

# Optional, explicit Inbox sensors. Gmail categories are server-side
# classifications queried with X-GM-RAW; they are not IMAP folders.
GMAIL_INBOX_SENSOR_PRESETS: dict[str, dict[str, Any]] = {
    "primary_unread": {
        "name": "Primary unread",
        "filters": {"read_state": "unread", "gmail_category": "primary"},
    },
    "important_unread": {
        "name": "Important unread",
        "filters": {"read_state": "unread", "important_state": "important"},
    },
    "starred_unread": {
        "name": "Starred unread",
        "filters": {"read_state": "unread", "starred_state": "starred"},
    },
    "promotions_unread": {
        "name": "Promotions unread",
        "filters": {"read_state": "unread", "gmail_category": "promotions"},
    },
    "social_unread": {
        "name": "Social unread",
        "filters": {"read_state": "unread", "gmail_category": "social"},
    },
    "updates_unread": {
        "name": "Updates unread",
        "filters": {"read_state": "unread", "gmail_category": "updates"},
    },
    "forums_unread": {
        "name": "Forums unread",
        "filters": {"read_state": "unread", "gmail_category": "forums"},
    },
}


def validate_imap_folder(value: Any) -> str:
    """Return a non-empty folder name without IMAP command controls."""
    folder = _clean_text(value, "Folder")
    if folder is None:
        raise ValueError("Folder must not be empty")
    return folder


def _clean_text(value: Any, field: str) -> str | None:
    """Return a safe, stripped structured-search value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_SEARCH_CRITERIA_CHARS:
        raise ValueError(f"{field} is too long")
    if any(character in text for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field} contains invalid control characters")
    return text


def quote_imap_search_value(value: Any, field: str = "Search value") -> str:
    """Quote one structured value using the IMAP quoted-string rules."""
    text = _clean_text(value, field)
    if text is None:
        raise ValueError(f"{field} must not be empty")
    return f'"{text.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'


def _imap_date(value: Any, field: str) -> str | None:
    """Convert a date selector value to the locale-independent IMAP format."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = _clean_text(value, field)
        try:
            parsed = date.fromisoformat(text or "")
        except ValueError as err:
            raise ValueError(f"{field} must use YYYY-MM-DD") from err
    return f"{parsed.day:02d}-{_MONTHS[parsed.month - 1]}-{parsed.year:04d}"


def normalize_structured_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """Return populated structured filters in a response-safe form."""
    normalized: dict[str, Any] = {}
    for field in _TEXT_FILTERS:
        if value := _clean_text(filters.get(field), field):
            normalized[field] = value
    for field in _DATE_FILTERS:
        if value := filters.get(field):
            if isinstance(value, (date, datetime)):
                normalized[field] = value.isoformat()[:10]
            else:
                normalized[field] = str(value)
    for field, allowed in (
        ("read_state", READ_STATES),
        ("starred_state", STARRED_STATES),
        ("important_state", IMPORTANT_STATES),
    ):
        value = filters.get(field, "any")
        if value not in allowed:
            raise ValueError(f"Invalid {field}")
        if value != "any":
            normalized[field] = value
    category = filters.get("gmail_category")
    if category not in (None, "", "any"):
        if category not in GMAIL_CATEGORIES:
            raise ValueError("Invalid Gmail category")
        normalized["gmail_category"] = category
    return normalized


def validate_search_tokens(tokens: Sequence[str]) -> list[str]:
    """Validate an already-tokenized IMAP search without reparsing it."""
    result = [str(token) for token in tokens]
    if not result:
        raise ValueError("Search criteria must not be empty")
    if len(result) > MAX_SEARCH_TOKENS:
        raise ValueError("Search criteria are too complex")
    if sum(len(token) for token in result) > MAX_SEARCH_CRITERIA_CHARS:
        raise ValueError("Search criteria are too long")
    if any(
        character in token for token in result for character in ("\r", "\n", "\x00")
    ):
        raise ValueError("Search criteria contain invalid control characters")
    return result


def build_structured_search_tokens(filters: Mapping[str, Any]) -> list[str]:
    """Translate populated structured filters to AND-combined IMAP tokens."""
    normalized = normalize_structured_filters(filters)
    tokens: list[str] = []
    for field, criterion in _TEXT_FILTERS.items():
        if value := normalized.get(field):
            tokens.extend((criterion, quote_imap_search_value(value, field)))

    read_state = normalized.get("read_state")
    if read_state == "unread":
        tokens.append("UNSEEN")
    elif read_state == "read":
        tokens.append("SEEN")

    starred_state = normalized.get("starred_state")
    if starred_state == "starred":
        tokens.append("FLAGGED")
    elif starred_state == "not_starred":
        tokens.append("UNFLAGGED")

    for field, criterion in _DATE_FILTERS.items():
        if formatted := _imap_date(normalized.get(field), field):
            tokens.extend((criterion, formatted))

    gmail_terms: list[str] = []
    if category := normalized.get("gmail_category"):
        gmail_terms.append(f"category:{category}")
    important_state = normalized.get("important_state")
    if important_state == "important":
        gmail_terms.append("is:important")
    elif important_state == "not_important":
        gmail_terms.append("-is:important")
    if gmail_terms:
        tokens.extend(
            ("X-GM-RAW", quote_imap_search_value(" ".join(gmail_terms), "Gmail filter"))
        )

    return validate_search_tokens(tokens or ["ALL"])
