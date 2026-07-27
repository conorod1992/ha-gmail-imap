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
- discoverable New email event entity plus the backwards-compatible
  `email_ha_new_email` bus event, without complete message bodies;
- `email_ha.find_emails` for UI-friendly structured searches;
- `email_ha.search_emails` for advanced raw IMAP searches;
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

The existing unread sensor still means IMAP `UNSEEN` in the configured folder
(normally `INBOX`). Its unique ID remains `<config-entry-id>_unread_count`, so
existing automations and entity customisations are not silently redefined.

### Gmail Inbox versus Primary

Gmail's IMAP `INBOX` represents every message carrying the Inbox label. It can
therefore include messages Gmail's web interface classifies as Primary,
Promotions, Social, Updates, or Forums. An IMAP unread count of 340 can
legitimately coexist with 0 unread in the web UI's Primary tab.

Gmail does not expose category tabs as ordinary IMAP folders. It does,
however, officially extend IMAP `SEARCH` with `X-GM-RAW`, whose single argument
uses Gmail search syntax. Email HA uses that documented extension only when a
configured structured search selects a Gmail category or importance state. For
example, Primary + Unread becomes the AND-combined IMAP search tokens
`UNSEEN X-GM-RAW "category:primary"`. This reflects Gmail's category
classification; a web UI setting such as **Include starred in Primary** can
make the visible tab differ from a pure `category:primary` count.

See Google's [IMAP extension documentation](https://developers.google.com/workspace/gmail/imap/imap-extensions)
and [category behavior](https://support.google.com/mail/answer/3094499).

The supported classifications map to IMAP as follows:

| Gmail view | Reliable IMAP query | Notes |
|---|---|---|
| Primary | `X-GM-RAW "category:primary"` | Gmail classification, not a folder or flag |
| Promotions | `X-GM-RAW "category:promotions"` | Gmail classification |
| Social | `X-GM-RAW "category:social"` | Gmail classification |
| Updates | `X-GM-RAW "category:updates"` | Gmail classification |
| Forums | `X-GM-RAW "category:forums"` | Gmail classification |
| Important | `X-GM-RAW "is:important"` | Gmail importance classification; Gmail also exposes labels through `X-GM-LABELS` |
| Starred | `FLAGGED` | Standard IMAP `\Flagged` state |

The category queries are reliable Gmail server-side classifications because
Google documents both `X-GM-RAW` and the listed `category:` operators. They are
Gmail-specific and are not presented as portable IMAP behavior.

### Optional email search sensors

After connecting an account, open its **Configure** dialog. Choose **Add Gmail
inbox sensors** for convenient optional presets: Primary unread, Important
unread, Starred unread, Promotions unread, Social unread, Updates unread, or
Forums unread. Select only the sensors you want; none are created by default.

Choose **Add email search sensor** for a custom sensor with a user-facing name,
folder, and the same structured filters as `find_emails`. For example,
configure `RSA unread` with folder `INBOX`, From contains `rsa.ie`, and Read
state `unread`; its state is the number of matching messages.

All optional sensors run through the account's existing coordinator and IMAP
session. They use server-side `UID SEARCH`, count returned UIDs, and do not
fetch headers or message bodies. Their attributes contain only the folder,
filter field names, and newest matching UID—not filter values or email bodies.
Up to 20 may be configured per account to keep refresh work bounded.

### New-email automations

Each account exposes a **New email** event entity on its Gmail device. On Home
Assistant 2026.7 or newer, choose **Event received** in the automation editor,
select that entity, and select the `new_email` event type. For YAML automations:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.gmail_example_new_email
    options:
      event_type:
        - new_email
actions:
  - action: persistent_notification.create
    data:
      title: New email
      message: >-
        {{ trigger.to_state.attributes.subject or '(no subject)' }}
```

Replace the example entity ID with the entity created for your account. The
event entity exposes bounded attributes including account, folder, UID,
subject, sender, and date, never a complete body. It fires only after the
initial refresh has established a UID baseline.

For backwards compatibility, the integration also continues to fire the raw
`email_ha_new_email` bus event with the same payload. Existing automations using
the **Manual event received** trigger do not need to change; that trigger also
remains the compatible option on older supported Home Assistant releases.

## Actions

All actions return dictionaries and arrays suitable for `response_variable`.

### `email_ha.find_emails`

Use this action for normal automations. Populated fields combine with logical
AND and are converted to validated IMAP tokens without constructing and
reparsing a raw command string.

```yaml
action: email_ha.find_emails
data:
  from: notifications@example.com
  subject: booking
  read_state: unread
  since: "2026-07-01"
  max_results: 10
response_variable: email_results
```

Supported filters include From, To, CC, Subject, server-side Body, server-side
Text, read state, starred state, Gmail category/importance, and date-based
Since, Before, or On. IMAP date searches have day resolution. `include_body`
defaults to false, and `body_max_chars` is ignored unless body inclusion is
enabled.

The response includes `account`, `folder`, the populated `filters`, `count`,
`emails`, and `truncated`. Message dictionaries use the shape documented below.

### `email_ha.search_emails` (advanced IMAP)

This existing action remains backwards compatible and accepts raw standard
IMAP criteria for advanced searches. Gmail web syntax such as
`from:person@example.com is:unread category:primary` is not standard IMAP
SEARCH syntax. Gmail-specific raw searches must explicitly use the documented
`X-GM-RAW` extension.

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

Both search actions share the same lower-level fetch and MIME parser. Tests
cover the complete action setting path: false performs a header-only
`BODY.PEEK[HEADER]` fetch, while true performs bounded `BODY.PEEK[]` retrieval
and returns `plain_text_body`, its compatibility alias `body_text`, and
`body_truncated`.

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
- Category and importance filters require Gmail's documented `X-GM-RAW` IMAP
  extension; they are not offered as portable behavior for other IMAP servers.
- Gmail's optional **Include starred in Primary** presentation setting is not
  part of the `category:primary` classification query.
- UIDs are folder-specific; no native Gmail thread object is exposed.
- Header-only searches cannot report previews or attachment metadata without
  fetching message content.
- Messages larger than the 2 MB retrieval cap are parsed on a best-effort basis
  and may report incomplete content or attachment metadata.
- Last-received/today-count entities were not added because optional search
  sensors already cover the count use cases without multiplying default
  entities. Persistent multi-message event replay, safe HTML output,
  recent-hours search, thread reconstruction, and diagnostics remain deferred.

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
