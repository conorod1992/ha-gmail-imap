"""Safe structured IMAP search helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

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
GMAIL_CATEGORIES = ("primary", "updates", "promotions", "social", "forums")
READ_STATES = ("any", "unread", "read")
STARRED_STATES = ("any", "starred", "not_starred")
IMPORTANT_STATES = ("any", "important", "not_important")
ATTACHMENT_STATES = ("any", "has_attachment", "no_attachment")
RELATIVE_DATE_RANGES = (
    "any",
    "today",
    "yesterday",
    "last_24_hours",
    "last_7_days",
    "last_30_days",
)
MATCH_MODES = ("all", "any")


def summarize_structured_filters(
    filters: Mapping[str, Any], *, folder: str | None = None, short: bool = False
) -> str:
    """Return a concise owner-facing description of every active filter."""
    normalized = normalize_structured_filters(filters)
    match_any = normalized.get("match_mode") == "any"
    details: list[str] = []
    if folder:
        details.append("Inbox" if folder.upper() == "INBOX" else folder)
    labels = {
        "from": "From contains",
        "to": "To contains",
        "cc": "Cc contains",
        "subject": "Subject contains",
        "body": "Body contains",
        "text": "Text contains",
        "attachment_filename": "Attachment name contains",
        "since": "Since",
        "before": "Before",
        "on": "On",
    }
    states = {
        "read_state": {"unread": "Unread", "read": "Read"},
        "gmail_category": {
            value: f"Category {value.title()}" for value in GMAIL_CATEGORIES
        },
        "important_state": {"important": "Important", "not_important": "Not important"},
        "starred_state": {"starred": "Starred", "not_starred": "Not starred"},
        "attachment_state": {
            "has_attachment": "Has attachment",
            "no_attachment": "No attachment",
        },
        "relative_date": {
            "today": "Received today",
            "yesterday": "Received yesterday",
            "last_24_hours": "Received in the last 24 hours",
            "last_7_days": "Received in the last 7 days",
            "last_30_days": "Received in the last 30 days",
        },
    }
    details.extend(
        states[field][value] for field in states if (value := normalized.get(field))
    )
    details.extend(
        f"{label} {value}" if field in _DATE_FILTERS else f'{label} "{value}"'
        for field, label in labels.items()
        if (value := normalized.get(field))
    )
    if not details:
        return "All email"
    prefix = "Match any condition · " if match_any else ""
    if short and len(details) > 4:
        return prefix + " · ".join(details[:4]) + f" · +{len(details) - 4} more"
    return prefix + " · ".join(details)


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
    match_mode = filters.get("match_mode", "all")
    if match_mode not in MATCH_MODES:
        raise ValueError("Invalid match_mode")
    if match_mode != "all":
        normalized["match_mode"] = match_mode
    for field in _TEXT_FILTERS:
        if value := _clean_text(filters.get(field), field):
            normalized[field] = value
    if value := _clean_text(filters.get("attachment_filename"), "attachment_filename"):
        normalized["attachment_filename"] = value
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
        ("attachment_state", ATTACHMENT_STATES),
        ("relative_date", RELATIVE_DATE_RANGES),
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
    if normalized.get("relative_date") and any(
        normalized.get(field) for field in _DATE_FILTERS
    ):
        raise ValueError("Relative date cannot be combined with exact date filters")
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


def _build_any_search_tokens(
    filters: Mapping[str, Any], *, current_date: date | None = None
) -> list[str]:
    """Build a nested IMAP OR expression from independent structured conditions."""
    clauses = [
        build_structured_search_tokens({field: value}, current_date=current_date)
        for field, value in filters.items()
    ]
    if not clauses:
        return ["ALL"]
    if len(clauses) == 1:
        return clauses[0]

    def grouped(clause: Sequence[str]) -> str:
        return clause[0] if len(clause) == 1 else f"({' '.join(clause)})"

    combined = clauses[-1]
    for clause in reversed(clauses[:-1]):
        combined = ["OR", grouped(clause), grouped(combined)]
    return validate_search_tokens(combined)


def build_structured_search_tokens(
    filters: Mapping[str, Any], *, current_date: date | None = None
) -> list[str]:
    """Translate populated structured filters to AND-combined IMAP tokens."""
    normalized = normalize_structured_filters(filters)
    if normalized.pop("match_mode", "all") == "any":
        return _build_any_search_tokens(normalized, current_date=current_date)

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
    relative_date = normalized.get("relative_date")
    today = current_date or dt_util.now().date()
    if relative_date == "today":
        tokens.extend(("SINCE", _imap_date(today, "relative_date") or ""))
    elif relative_date == "yesterday":
        yesterday = today - timedelta(days=1)
        tokens.extend(
            (
                "SINCE",
                _imap_date(yesterday, "relative_date") or "",
                "BEFORE",
                _imap_date(today, "relative_date") or "",
            )
        )
    elif relative_date == "last_24_hours":
        gmail_terms.append("newer_than:1d")
    elif relative_date == "last_7_days":
        gmail_terms.append("newer_than:7d")
    elif relative_date == "last_30_days":
        gmail_terms.append("newer_than:30d")

    if category := normalized.get("gmail_category"):
        gmail_terms.append(f"category:{category}")
    important_state = normalized.get("important_state")
    if important_state == "important":
        gmail_terms.append("is:important")
    elif important_state == "not_important":
        gmail_terms.append("-is:important")
    attachment_state = normalized.get("attachment_state")
    if attachment_state == "has_attachment":
        gmail_terms.append("has:attachment")
    elif attachment_state == "no_attachment":
        gmail_terms.append("-has:attachment")
    if attachment_filename := normalized.get("attachment_filename"):
        escaped = attachment_filename.replace(chr(92), chr(92) * 2).replace(
            chr(34), chr(92) + chr(34)
        )
        gmail_terms.append(f'filename:"{escaped}"')
    if gmail_terms:
        tokens.extend(
            ("X-GM-RAW", quote_imap_search_value(" ".join(gmail_terms), "Gmail filter"))
        )

    return validate_search_tokens(tokens or ["ALL"])
