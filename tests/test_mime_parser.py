"""Tests for bounded MIME parsing."""

from __future__ import annotations

from custom_components.email_ha.mime_parser import parse_email_bytes


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
        b"Content-Transfer-Encoding: base64\r\n\r\nUERGREFUQQ==\r\n"
        b"--x--\r\n"
    )
    result = parse_email_bytes(
        raw, "8", "INBOX", include_body=True, body_max_chars=1000
    )

    assert result["has_attachments"] is True
    assert result["attachment_count"] == 1
    assert result["attachments"] == [
        {
            "filename": "document.pdf",
            "content_type": "application/pdf",
            "content_disposition": "attachment",
            "content_id": None,
            "size": None,
        }
    ]
    assert "data" not in result["attachments"][0]


def test_header_only_parse_does_not_expose_body() -> None:
    """A metadata-only parse must not return body content."""
    result = parse_email_bytes(
        b"From: sender@example.com\r\nSubject: Secret\r\n\r\nbody",
        "9",
        "INBOX",
    )

    assert result["preview"] == ""
    assert "plain_text_body" not in result
    assert "body_text" not in result
