# Email HA: read-only Gmail IMAP for Home Assistant

Email HA connects Home Assistant to Gmail through OAuth 2.0 and IMAP. It
provides mailbox sensors, a new-email event, and structured response actions
for locating and explicitly retrieving email. The integration domain remains
`email_ha` for compatibility.

This project is a fork of
[PineappleEmperor/email-ha](https://github.com/PineappleEmperor/email-ha) and
retains its licence and attribution.

## Security model

- Gmail access uses TLS on `imap.gmail.com:993` and XOAUTH2.
- Every folder is opened read-only and retrieval uses `BODY.PEEK`, so actions do
  not intentionally change `\Seen` or other flags.
- There are no send, reply, forward, delete, move, label, flag, attachment
  download, or other mailbox mutation actions.
- Search returns headers by default. Message bodies are retrieved only when
  explicitly requested and are capped at 20,000 characters.
- IMAP header fetches are capped at 64 KiB and explicit message fetches at 2 MB
  before parsing, limiting exposure to maliciously oversized messages.
- Attachment metadata may be returned, but attachment bytes are never returned,
  logged, or saved.
- HTML is converted locally to readable plain text. Scripts, images, links, and
  other remote resources are never loaded. Raw or sanitized HTML is not exposed.

Google documents `https://mail.google.com/` as the scope for ordinary IMAP,
POP, and SMTP OAuth access. The narrower Gmail API read-only scope does not
authenticate a normal IMAP session. See Google's
[XOAUTH2 protocol documentation](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol).

## Features

- unread, total, folder-count, and latest-email sensors;
- `email_ha_new_email` event without complete message bodies;
- `email_ha.search_emails` for bounded standard IMAP searches;
- `email_ha.get_message` for explicit folder-specific retrieval;
- backwards-compatible `email_ha.query_emails` action;
- multiple Gmail accounts with explicit account selection when ambiguous.

Thread reconstruction and recent-hours convenience actions are intentionally
deferred from this first secure retrieval phase.

## Installation

### HACS custom repository

This fork is not claimed to be in the default HACS catalogue.

1. Open HACS, choose **Integrations**, then **Custom repositories**.
2. Add `https://github.com/conorod1992/ha-gmail-imap` as an **Integration**.
3. Install **Email HA** and restart Home Assistant.

To update later, use HACS' update action and restart Home Assistant when asked.

### Manual

Copy `custom_components/email_ha` into
`<home-assistant-config>/custom_components/email_ha`, then restart Home
Assistant.

## Google OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select
   a project and configure its OAuth consent screen.
2. If the app is in testing mode, add every Gmail account that will connect as a
   test user.
3. Create an OAuth 2.0 client of type **Web application**.
4. Add Home Assistant's external callback URL as an authorized redirect URI:
   `https://my.home-assistant.io/redirect/oauth`
5. In Home Assistant, open **Settings > Devices & services > Application
   credentials**, add credentials for **Email HA**, and enter the client ID and
   client secret.
6. Add the Email HA integration and complete Google's consent flow.

The Gmail REST API is not called by this integration; it uses Gmail's IMAP
OAuth protocol. Creating the OAuth consent screen and web client is required,
but enabling the Gmail API is not required for the implemented IMAP transport.
Home Assistant's redirect helper forwards to the instance's
`/auth/external/callback` endpoint. Ensure the instance's internal/external URL
configuration allows the browser to return to Home Assistant.

Tokens are held in Home Assistant's config entry by the built-in OAuth helper.
The helper refreshes expired access tokens using the stored refresh token.
If access is revoked, use **Reconfigure/Reauthenticate** on the integration.

## Accounts, sensors, and events

Each Gmail address is a separate config entry and may reuse the same Application
Credentials client. If exactly one loaded account exists, action calls may omit
`config_entry_id`. With multiple accounts, select one; no arbitrary default is
chosen.

Existing config entries and entity unique IDs are preserved. Sensors include:

- unread count;
- total message count;
- selectable folder count and names;
- latest email subject and bounded metadata.

`email_ha_new_email` fires when a newer folder UID is observed after the initial
refresh. Its payload includes account/folder and the same bounded metadata used
by polling, never a complete body.

## Actions

All actions return dictionaries and arrays suitable for `response_variable`.

### `email_ha.search_emails`

Use this first to identify likely messages. Search criteria are standard IMAP
criteria, not Gmail web/API search syntax.

| Field | Default | Limits |
|---|---:|---:|
| `config_entry_id` | single loaded account | required if ambiguous |
| `folder` | `INBOX` | non-empty folder name |
| `search_criteria` | `ALL` | 1,000 characters / 40 tokens |
| `max_results` | `10` | 1-25 |
| `include_body` | `false` | opt-in private content |
| `body_max_chars` | `4000` | 1-20,000 per message |

```yaml
action: email_ha.search_emails
data:
  search_criteria: 'UNSEEN FROM "example@example.com"'
  max_results: 5
response_variable: email_results
```

Response shape:

```yaml
account: example@gmail.com
folder: INBOX
search_criteria: 'UNSEEN FROM "example@example.com"'
count: 1
emails:
  - uid: "12345"
    message_id: "<message-id@example.com>"
    in_reply_to: null
    references: []
    subject: Example
    sender:
      name: Example Sender
      address: sender@example.com
    to: []
    cc: []
    reply_to: []
    date: "2026-07-24T15:30:00+01:00"
    internal_date: "24-Jul-2026 15:30:01 +0100"
    flags: []
    folder: INBOX
    preview: ""
    has_attachments: false
    attachments: []
truncated: false
```

Without `include_body`, only the RFC header block is fetched, so `preview` is
empty and attachment presence is unknown. With `include_body: true`, the
response additionally includes `plain_text_body`, `body_text` (legacy alias),
`body_truncated`, a preview, and attachment metadata.

Useful IMAP criteria:

| Goal | Criteria |
|---|---|
| Everything | `ALL` |
| Unread | `UNSEEN` |
| Sender | `FROM "person@example.com"` |
| Subject | `SUBJECT "renewal"` |
| Since an IMAP date | `SINCE 01-Jul-2026` |
| Combined | `UNSEEN FROM "person@example.com"` |

### `email_ha.get_message`

Use this only after identifying a specific message. An IMAP UID is unique only
within its folder and may change when a message is copied or moved.

| Field | Default | Limits |
|---|---:|---:|
| `config_entry_id` | single loaded account | required if ambiguous |
| `folder` | `INBOX` | folder containing the UID |
| `uid` | required | decimal UID |
| `body_max_chars` | `12000` | 1-20,000 |

```yaml
action: email_ha.get_message
data:
  folder: INBOX
  uid: "{{ email_results.emails[0].uid }}"
  body_max_chars: 12000
response_variable: email_message
```

The response contains `account`, `folder`, and `message`. The message uses the
same metadata keys as search plus `plain_text_body`, `body_text`,
`body_truncated`, and attachment metadata. HTML is not returned.

### `email_ha.query_emails` (legacy)

Existing automations keep the action name, existing fields, and top-level
`emails` key. It now uses the same read-only parser and safety limits as search.
`include_attachments` remains accepted, but attachment data is no longer
returned; only metadata is available when `include_full_body` is true. The
maximum result count is now 25 as a deliberate privacy/resource bound.

```yaml
action: email_ha.query_emails
data:
  folder: INBOX
  search_criteria: 'SUBJECT "invoice"'
  max_results: 5
response_variable: legacy_results
```

## Privacy and troubleshooting

- Action responses can contain private email. Expose these actions selectively
  to conversation agents and keep result/body limits small.
- Search errors usually mean malformed criteria or an inaccessible folder.
- Authentication errors require reauthentication; never paste tokens into logs
  or issue reports.
- If setup cannot return from Google, verify Home Assistant's URL settings and
  the OAuth redirect URI.
- If IMAP is restricted by a Google Workspace administrator, ask that
  administrator to allow the account/client.

For temporary debugging:

```yaml
logger:
  logs:
    custom_components.email_ha: debug
```

Restart after changing logging. Review logs before sharing them; this
integration avoids bodies and tokens, but account identifiers and operational
metadata may still be sensitive.

## Known limitations

- This phase is Gmail-specific despite using standard IMAP primitives.
- Search order is newest UID first, which is normally arrival order but is not a
  universal date sort.
- IMAP `SEARCH` dates have day resolution and are not Gmail web-search syntax.
- UIDs are folder-specific; no native Gmail thread object is exposed.
- Header-only searches cannot report previews or attachment metadata without
  fetching message content.
- Messages larger than the 2 MB retrieval cap are parsed on a best-effort basis
  and may report incomplete content or attachment metadata.
- Safe HTML output, recent-hours search, thread reconstruction, diagnostics,
  and expanded config-flow tests are deferred.

## Development validation

The repository provides pytest requirements and Ruff configuration. Typical
checks are:

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pyright
```

GitHub Actions also run HACS and Hassfest validation.
