"""Tests for bounded MIME parsing."""

from __future__ import annotations

from custom_components.email_ha.mime_parser import (
    MAX_ATTACHMENT_FILENAME_CHARS,
    MAX_ATTACHMENT_METADATA,
    parse_email_bytes,
)


def test_plain_message_metadata_and_truncation() -> None:
    """Decode headers, addresses, thread fields, and bounded plain text."""
    raw = (
        b"From: =?utf-8?q?Example_Sender?= <sender@example.com>\r\n"
        b"To: Conor <conor@example.com>\r\n"
        b"Subject: =?utf-8?q?Quarterly_=E2=9C=93?=\r\n"
        b"Date: Fri, 24 Jul 2026 15:30:00 +0100\r\n"
        b"Message-ID: <child@example.com>\r\n"
        b"In-Reply-To: <parent@example.com>\r\n"
        b"References: <first@example.com> <parent@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"First paragraph.\r\n\r\nSecond paragraph."
    )
    result = parse_email_bytes(
        raw, "123", "INBOX", include_body=True, body_max_chars=20
    )

    assert result["subject"] == "Quarterly ✓"
    assert result["sender"] == {
        "name": "Example Sender",
        "address": "sender@example.com",
    }
    assert result["message_id"] == "<child@example.com>"
    assert result["in_reply_to"] == "<parent@example.com>"
    assert result["references"] == [
        "<first@example.com>",
        "<parent@example.com>",
    ]
    assert result["plain_text_body"] == "First paragraph.\n\nSe"
    assert result["body_truncated"] is True
    assert result["attachment_metadata_available"] is True
    assert result["attachment_count"] == 0
    assert "body_text" not in result


def test_html_only_message_is_text_without_active_or_remote_content() -> None:
    """Convert HTML locally without returning scripts or tracking URLs."""
    raw = (
        b"From: sender@example.com\r\n"
        b"Subject: HTML only\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><head><script>alert('x')</script></head>"
        b"<body><p>Hello <b>world</b>.</p>"
        b"<img src='https://tracker.example/pixel'>"
        b"<a href='https://remote.example'>Read</a></body></html>"
    )
    result = parse_email_bytes(
        raw, "5", "INBOX", include_body=True, body_max_chars=1000
    )

    assert result["plain_text_body"] == "Hello world.\nRead"
    assert "alert" not in result["plain_text_body"]
    assert "https://" not in result["plain_text_body"]
    assert "html_body" not in result


def test_attachment_metadata_never_contains_payload() -> None:
    """Return attachment metadata without bytes or base64 content."""
    raw = (
        b"From: sender@example.com\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/plain\r\n\r\nHello\r\n"
        b"--x\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=document.pdf\r\n"
        b"Content-ID: <document@example.com>\r\n"
        b"Content-Length: 12345\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\nUERGREFUQQ==\r\n"
        b"--x--\r\n"
    )
    result = parse_email_bytes(
        raw, "8", "INBOX", include_body=True, body_max_chars=1000
    )

    assert result["attachment_metadata_available"] is True
    assert result["attachment_metadata_unavailable_reason"] is None
    assert result["has_attachments"] is True
    assert result["attachment_count"] == 1
    assert result["attachments_truncated"] is False
    assert result["attachments"] == [
        {
            "part_id": "2",
            "filename": "document.pdf",
            "content_type": "application/pdf",
            "content_disposition": "attachment",
            "content_id": "<document@example.com>",
            "size": 12345,
        }
    ]
    assert "data" not in result["attachments"][0]
    assert "payload" not in result["attachments"][0]


def test_nested_multipart_uses_stable_part_paths() -> None:
    """Nested MIME attachments receive stable 1-based part identifiers."""
    raw = (
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
        b"--outer\r\nContent-Type: multipart/alternative; boundary=inner\r\n\r\n"
        b"--inner\r\nContent-Type: text/plain\r\n\r\nHello\r\n"
        b"--inner\r\nContent-Type: text/html\r\n\r\n<p>Hello</p>\r\n"
        b"--inner--\r\n"
        b"--outer\r\nContent-Type: image/png\r\n"
        b"Content-Disposition: inline\r\nContent-ID: <logo>\r\n\r\nPNGDATA\r\n"
        b"--outer\r\nContent-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment\r\n\r\nBINARY\r\n"
        b"--outer--\r\n"
    )
    result = parse_email_bytes(raw, "11", "INBOX", include_body=True)

    assert [item["part_id"] for item in result["attachments"]] == ["2", "3"]
    assert result["attachments"][0]["content_id"] == "<logo>"
    assert result["attachments"][1]["filename"] is None


def test_attachment_metadata_is_bounded() -> None:
    """Attachment lists and long filenames are capped without reading payloads."""
    long_name = "a" * (MAX_ATTACHMENT_FILENAME_CHARS + 50) + ".txt"
    parts = []
    for index in range(MAX_ATTACHMENT_METADATA + 3):
        filename = long_name if index == 0 else f"file-{index}.txt"
        parts.append(
            f"--x\r\nContent-Type: text/plain\r\n"
            f"Content-Disposition: attachment; filename={filename}\r\n\r\n"
            f"ignored-{index}\r\n"
        )
    raw = (
        "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n"
        + "".join(parts)
        + "--x--\r\n"
    ).encode()

    result = parse_email_bytes(raw, "12", "INBOX", include_body=True)

    assert result["attachment_count"] == MAX_ATTACHMENT_METADATA + 3
    assert len(result["attachments"]) == MAX_ATTACHMENT_METADATA
    assert result["attachments_truncated"] is True
    assert len(result["attachments"][0]["filename"]) == MAX_ATTACHMENT_FILENAME_CHARS


def test_header_only_parse_explicitly_marks_attachment_metadata_unavailable() -> None:
    """Header-only results never imply that body or attachment data was fetched."""
    result = parse_email_bytes(
        b"From: sender@example.com\r\n"
        b"Subject: Secret\r\n"
        b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"body",
        "9",
        "INBOX",
    )

    assert result["preview"] == ""
    assert result["attachment_metadata_available"] is False
    assert result["attachment_metadata_unavailable_reason"] == "header_only"
    assert result["has_attachments"] is None
    assert result["attachment_count"] is None
    assert result["attachments_truncated"] is None
    assert result["attachments"] == []
    assert "plain_text_body" not in result
    assert "body_text" not in result
