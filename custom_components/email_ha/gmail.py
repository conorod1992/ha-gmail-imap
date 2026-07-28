"""Canonical definitions for built-in Gmail entities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from homeassistant.config_entries import ConfigEntry

from .const import CONF_GMAIL_ENTITIES


@dataclass(frozen=True, slots=True)
class GmailEntityDefinition:
    """Describe one fixed Gmail-facing entity."""

    key: str
    platform: Literal["sensor", "event"]
    source: str
    icon: str
    filters: Mapping[str, Any] = field(default_factory=dict)


GMAIL_ENTITY_DEFINITIONS: tuple[GmailEntityDefinition, ...] = (
    GmailEntityDefinition(
        "primary_unread",
        "sensor",
        "search_count",
        "mdi:inbox-arrow-down-outline",
        {"read_state": "unread", "gmail_category": "primary"},
    ),
    GmailEntityDefinition(
        "latest_email", "sensor", "latest_email", "mdi:email-outline"
    ),
    GmailEntityDefinition("new_email", "event", "new_email", "mdi:email-fast-outline"),
    GmailEntityDefinition(
        "inbox_unread", "sensor", "inbox_unread", "mdi:email-outline"
    ),
    GmailEntityDefinition(
        "important_unread",
        "sensor",
        "search_count",
        "mdi:label-important-outline",
        {"read_state": "unread", "important_state": "important"},
    ),
    GmailEntityDefinition(
        "starred_unread",
        "sensor",
        "search_count",
        "mdi:star-outline",
        {"read_state": "unread", "starred_state": "starred"},
    ),
    GmailEntityDefinition(
        "updates_unread",
        "sensor",
        "search_count",
        "mdi:information-outline",
        {"read_state": "unread", "gmail_category": "updates"},
    ),
    GmailEntityDefinition(
        "promotions_unread",
        "sensor",
        "search_count",
        "mdi:tag-outline",
        {"read_state": "unread", "gmail_category": "promotions"},
    ),
    GmailEntityDefinition(
        "social_unread",
        "sensor",
        "search_count",
        "mdi:account-group-outline",
        {"read_state": "unread", "gmail_category": "social"},
    ),
    GmailEntityDefinition(
        "forums_unread",
        "sensor",
        "search_count",
        "mdi:forum-outline",
        {"read_state": "unread", "gmail_category": "forums"},
    ),
    GmailEntityDefinition(
        "inbox_messages", "sensor", "inbox_total", "mdi:email-multiple-outline"
    ),
    GmailEntityDefinition(
        "mailbox_folders",
        "sensor",
        "folder_count",
        "mdi:folder-multiple-outline",
    ),
)

GMAIL_ENTITIES = {definition.key: definition for definition in GMAIL_ENTITY_DEFINITIONS}
GMAIL_SENSOR_DEFINITIONS = tuple(
    definition
    for definition in GMAIL_ENTITY_DEFINITIONS
    if definition.platform == "sensor"
)
GMAIL_SEARCH_DEFINITIONS = tuple(
    definition
    for definition in GMAIL_SENSOR_DEFINITIONS
    if definition.source == "search_count"
)
DEFAULT_GMAIL_ENTITIES = ("primary_unread", "latest_email", "new_email")


def normalize_enabled_entities(value: Any) -> set[str]:
    """Return a valid entity-key set, falling back to recommended defaults."""
    if value is None:
        return set(DEFAULT_GMAIL_ENTITIES)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return set(DEFAULT_GMAIL_ENTITIES)
    return {str(key) for key in value if str(key) in GMAIL_ENTITIES}


def enabled_entities_for_entry(entry: ConfigEntry) -> set[str]:
    """Return the configured fixed entity set for one account."""
    value = entry.options.get(CONF_GMAIL_ENTITIES, entry.data.get(CONF_GMAIL_ENTITIES))
    return normalize_enabled_entities(value)
