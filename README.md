# Email HA

Read-only Gmail integration for Home Assistant.

Email HA signs in through Google OAuth, listens for new mail with Gmail IMAP
IDLE, and exposes useful Gmail concepts without requiring ordinary users to
understand IMAP.

## What you get

- Primary unread and other Gmail category, Important, and Starred sensors
- Latest email metadata
- a New email event entity for UI-built automations
- custom server-side email count sensors
- per-filter Email watch event entities
- a friendly `email_ha.find_emails` action
- explicit, bounded email-body retrieval
- an advanced raw IMAP search action
- multiple Gmail accounts

The recommended first setup enables **Primary unread**, **Latest email**, and
**New email**. All other built-in entities remain available from **Configure >
Gmail sensors**.

## Installation

### HACS custom repository

1. Open HACS and choose **Integrations > Custom repositories**.
2. Add `https://github.com/conorod1992/ha-gmail-imap` as an **Integration**.
3. Install **Email HA** and restart Home Assistant.

### Manual installation

Copy `custom_components/email_ha` into
`<home-assistant-config>/custom_components/email_ha`, then restart Home
Assistant.

## Google sign-in setup

Google requires each installation to provide its own OAuth client. Email HA
cannot safely put a shared client secret in a public repository, and Home
Assistant's application-credentials framework cannot remove this provider
requirement without a separately operated cloud account-linking service.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or
   select a project.
2. Open **Google Auth Platform** and configure the consent screen. For a
   personal project, choose an external audience. Add the Gmail account under
   **Test users** while the app is in testing.
3. Add the OAuth scope `https://mail.google.com/`. Google documents this scope
   for Gmail IMAP OAuth access. It is a broad mailbox scope; the read-only
   guarantee comes from Email HA exposing only read-only IMAP operations.
4. Open **APIs & Services > Credentials**, create an **OAuth client ID**, and
   select **Web application**.
5. Add this authorized redirect URI exactly:
   `https://my.home-assistant.io/redirect/oauth`
6. In Home Assistant, open **Settings > Devices & services > Application
   credentials**, choose **Add application credentials**, select **Email HA**,
   and paste the client ID and client secret.
7. Add the Email HA integration, enter the Gmail address, and sign in with
   Google.

Email HA does not call the Gmail REST API, so enabling the Gmail API is not a
prerequisite. The integration authenticates directly to `imap.gmail.com` using
OAuth2.

Personal Gmail accounts no longer have an Enable IMAP switch: Google states
that IMAP has been always enabled since January 2025. Google Workspace
administrators can still restrict IMAP for their organisation, so a managed
account may require its administrator to allow IMAP access. See Google's
[Gmail client guidance](https://support.google.com/mail/answer/7126229) and
[Workspace administrator guidance](https://support.google.com/a/answer/9003945).

If setup reports missing credentials, complete steps 1-6 first. If Google
rejects access, check the redirect URI, consent-screen status, requested scope,
and test-user list. A callback failure usually means Home Assistant's external
URL cannot return the browser to the instance.

## First setup

After Google sign-in, Email HA shows a simple Gmail entity list. Recommended
entities are already selected:

- **Primary unread** — unread mail Gmail classifies as Primary
- **Latest email** — subject and bounded header metadata for the newest email
- **New email** — an EventEntity for automations

Select any additional Gmail entities you already want, then finish setup. The
account appears as one service device named `Gmail - you@example.com`.

New mail normally arrives through Gmail IMAP IDLE. A fixed internal 15-minute
refresh provides resilience if a push is missed; there is no misleading poll
interval to configure.

## Gmail sensors

Open **Settings > Devices & services > Email HA > Configure > Gmail sensors**
to manage the desired entity set in one screen.

| Entity | Meaning | Recommended |
|---|---|---:|
| Primary unread | Unread messages Gmail classifies as Primary | Yes |
| Latest email | Newest email subject and selected header metadata | Yes |
| New email | Event entity for each newly detected email | Yes |
| Inbox unread | All unread messages carrying the Gmail Inbox label | No |
| Important unread | Unread mail Gmail classifies as Important | No |
| Starred unread | Unread mail with the Starred flag | No |
| Updates unread | Unread Updates mail | No |
| Promotions unread | Unread Promotions mail | No |
| Social unread | Unread Social mail | No |
| Forums unread | Unread Forums mail | No |
| Inbox messages | Total messages carrying the Inbox label | No |
| Mailbox folders | Count and list of selectable folders | No |

### Primary unread versus Inbox unread

**Primary unread** is what many people mean by useful unread inbox mail: Gmail
has classified it into the Primary category.

**Inbox unread** counts every unread message with the Inbox label, including
messages that may appear under Promotions, Social, Updates, or Forums. The two
numbers can therefore differ substantially.

Gmail category and importance counts use Gmail's documented `X-GM-RAW` IMAP
extension. Starred uses the standard IMAP `\Flagged` state. These details are
not needed for normal setup.

## Custom email sensors

Choose **Configure > Custom email sensors** to view, add, edit, duplicate, or
delete count sensors. The management list shows enough of each private filter
to identify it; entity attributes expose only filter field names, not values.

The first form contains common filters, including attachment presence. Turn on
**Add more filters** for To, CC, body text, any text, attachment filename, and
date filters. Blank fields are ignored and all filled conditions must match.

Examples:

| Name | Folder | Filters |
|---|---|---|
| RSA unread | Inbox | From contains `rsa.ie`; Read state `Unread` |
| Bookings | Inbox | Subject contains `booking`; Read state `Unread` |
| Starred Primary | Inbox | Gmail category `Primary`; Starred state `Starred` |

Gmail performs custom sensor searches server-side. Email HA retrieves the
matching UID count and bounded header metadata for the newest match; it does
not download bodies to calculate a count. Up to 20 custom sensors can be
configured per account.

Custom sensor attributes include `newest_matching_uid`, subject, sender name,
sender address, and date. These describe current mailbox state, including mail
that existed before Home Assistant started. `last_new_match` is different: it
is set only when Email HA observes a genuinely new arrival that matches the
sensor. It is runtime observation state and resets when the integration is
reloaded. Filter values remain private; attributes list filter field names
only.

Discovered folders are offered in the folder selector. An exact folder name
can still be entered for advanced or localised Gmail folder layouts.

## Email watches

Choose **Configure > Email watches** to add, edit, duplicate, or delete an
event stream for matching new mail. A custom sensor answers "how many messages
match now?" An Email watch answers "did a matching message just arrive?" Each
watch has its own discoverable EventEntity and keeps the same entity identity
when renamed. Up to 20 watches can be configured per account.

Watch event type `new_matching_email` includes account, folder, UID,
Message-ID, subject, sender name/address, date, watch ID, and watch name when
available. It never contains a body or filter values. Matching is constrained
to the newly detected folder-specific UID; attachments are never downloaded.

Watch baselines use Gmail UIDVALIDITY and UIDNEXT. Historical matches are not
fired at startup, after an integration reload, or when a watch is created.
Changing an old message or changing a historical count is not a new arrival.

### RSA email watch

Create a watch named **RSA emails** with **From contains** `rsa.ie` and
**Subject contains** `check test`, then select its event entity in an
automation:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.gmail_you_example_com_rsa_emails
    options:
      event_type:
        - new_matching_email
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: RSA email arrived
      message: "{{ trigger.to_state.attributes.subject }}"
```

### Booking watch without body retrieval

A **New booking** watch can use **Subject contains** `booking` and **Attachment
filename contains** `pdf`. The automation reacts using safe event metadata and
does not call `get_email_contents`:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.gmail_you_example_com_new_booking
    options:
      event_type:
        - new_matching_email
actions:
  - action: persistent_notification.create
    data:
      title: New booking email
      message: >-
        {{ trigger.to_state.attributes.sender_name }} sent
        {{ trigger.to_state.attributes.subject }}.
```

## Automations

### Notify when any new email arrives

In the automation editor, choose the **Event received** trigger, select the
account's **New email** entity, and select event type `new_email`.

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.gmail_you_example_com_new_email
    options:
      event_type:
        - new_email
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: New email
      message: >-
        {{ trigger.to_state.attributes.sender_name }}:
        {{ trigger.to_state.attributes.subject or '(no subject)' }}
```

Select the real entity in the UI instead of copying the example entity ID.
Event data contains account, folder, UID, Message-ID when available, subject,
sender name/address, and date. It never contains a message body.

### Notify only for a specific sender

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.gmail_you_example_com_new_email
    options:
      event_type:
        - new_email
conditions:
  - condition: template
    value_template: >-
      {{ 'rsa.ie' in (trigger.to_state.attributes.sender_address | lower) }}
actions:
  - action: notify.mobile_app_your_phone
    data:
      message: "{{ trigger.to_state.attributes.subject }}"
```

### Run when Primary unread becomes greater than zero

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.gmail_you_example_com_primary_unread
    above: 0
actions:
  - action: persistent_notification.create
    data:
      title: Primary Gmail
      message: You have unread Primary email.
```

### Find emails with a response variable

```yaml
actions:
  - action: email_ha.find_emails
    data:
      from: rsa.ie
      subject: renewal
      read_state: unread
      max_results: 5
    response_variable: found_mail
  - action: persistent_notification.create
    data:
      title: RSA email count
      message: "Found {{ found_mail.count }} matching emails."
```

### Find an email and explicitly retrieve its body

```yaml
actions:
  - action: email_ha.find_emails
    data:
      subject: booking
      max_results: 1
    response_variable: found_mail
  - if: "{{ found_mail.count > 0 }}"
    then:
      - action: email_ha.get_email_contents
        data:
          folder: "{{ found_mail.folder }}"
          uid: "{{ found_mail.emails[0].uid }}"
          body_max_chars: 4000
        response_variable: selected_email
      - action: persistent_notification.create
        data:
          title: "{{ selected_email.message.subject }}"
          message: "{{ selected_email.message.plain_text_body }}"
```

## Actions

### Find emails

`email_ha.find_emails` is the normal action. It provides friendly From, To, CC,
Subject, Body, Any text, read, starred, importance, category, and date fields.
Blank filters are ignored; filled filters combine with AND.

Attachment filters use Gmail's server-side search. **Attachment** accepts Any,
Has attachment, or No attachment. **Attachment filename contains** safely
builds Gmail's `filename:` criterion; Gmail decides substring/token matching,
so it is not a local MIME filename parser.

Find messages with any attachment:

```yaml
action: email_ha.find_emails
data:
  from: rsa.ie
  attachment_state: has_attachment
response_variable: attached_mail
```

Find messages whose attachment filename matches `pdf`:

```yaml
action: email_ha.find_emails
data:
  attachment_state: has_attachment
  attachment_filename: pdf
response_variable: pdf_mail
```

Searches return newest matches first and include metadata only by default. Set
`include_body: true` only when the automation needs bounded readable content.
The body limit is 1-20,000 characters per message and result count is limited
to 1-25. When MIME content is explicitly retrieved, results also include
`has_attachments`, `attachment_count`, and bounded filename/content-type
metadata; attachment payloads are never exposed.

### Get email contents

`email_ha.get_email_contents` retrieves one folder-specific UID returned by
Find emails or the New email event. It uses read-only `BODY.PEEK` and returns a
plain/readable bounded body plus attachment metadata. UIDs are valid only in
their folder.

### Advanced: Search using IMAP query

`email_ha.search_emails` accepts raw standard IMAP SEARCH criteria such as:

```yaml
action: email_ha.search_emails
data:
  folder: INBOX
  search_criteria: 'UNSEEN FROM "person@example.com"'
  max_results: 5
response_variable: advanced_results
```

This action is intended for users who already understand IMAP. Gmail web-search
syntax is not standard IMAP syntax; Gmail-specific raw expressions require the
documented `X-GM-RAW` extension.

## Multiple accounts

Add the integration once for each Gmail address. Accounts can reuse the same
Google OAuth client. Every account has its own device, coordinator, folder UID
baselines, New email entity, and Email watch entities. Events never cross
accounts.

Actions automatically use the only loaded account. When more than one account
is loaded, select **Account** in the action UI so no arbitrary account is used.

## Privacy and security

- OAuth2 tokens are managed by Home Assistant's application-credentials helper.
- Gmail IMAP requires Google's broad `https://mail.google.com/` OAuth scope;
  unlike a Gmail REST API scope, there is no narrower read-only IMAP scope. The
  integration enforces read-only behaviour by its implemented command surface.
- All Gmail traffic uses TLS and XOAUTH2.
- Folders are opened read-only with IMAP `EXAMINE`.
- Fetches use `BODY.PEEK`, so retrieval does not intentionally mark mail read.
- Count sensors use server-side searches and do not fetch message bodies.
- Find emails and advanced search omit bodies by default.
- Body retrieval is explicit, bounded to 20,000 readable characters, and capped
  at 2 MB of source message data before parsing.
- HTML is converted locally to readable text. Scripts, remote images, tracking
  pixels, styles, and links are not loaded.
- Attachment metadata may be returned; attachment bytes are never downloaded or
  exposed.
- Email HA has no send, reply, forward, delete, move, label, star, flag, or
  mark-read/unread operation.

Be deliberate when exposing action responses to notifications, logs,
conversation agents, or other automations because explicitly retrieved content
can be private.

## Advanced account and IMAP behaviour

**Configure > Advanced account settings** can change the folder used by Latest
email and New email. Inbox is recommended. Fixed Gmail count sensors keep their
documented Inbox meaning regardless of this setting.

Gmail IMAP UIDs are monotonically increasing only within one folder and one
UIDVALIDITY generation. Email HA records UIDVALIDITY and UIDNEXT on initial
startup, emits nothing for existing mail, and then fetches only UIDs above that
baseline. This prevents deleting or moving the newest email from making an
older message look new. Multiple arrivals are emitted oldest UID to newest UID.
To keep work bounded, a single refresh emits at most the newest 25 arrivals and
advances the baseline; skipped bursts are logged without replaying old mail.

IDLE reconnects retain the baseline. Reloading the integration establishes a
fresh baseline, so existing messages are not replayed. UIDs copied or moved to
another folder receive folder-specific identity and may be new within that
folder.

Google documents Gmail's category/importance search support in its
[IMAP extensions](https://developers.google.com/workspace/gmail/imap/imap-extensions).

## Troubleshooting

- **Missing application credentials:** complete the Google sign-in setup before
  adding the integration.
- **Google rejected access:** verify the consent screen, test user, exact scope,
  client secret, and redirect URI.
- **Managed account cannot connect:** ask the Workspace administrator whether
  IMAP and the OAuth app are allowed.
- **Folder unavailable:** choose a discovered folder under Configure or copy its
  exact IMAP identifier.
- **No immediate update:** Gmail IDLE is the normal path; the internal resilience
  refresh runs every 15 minutes.

Temporary debug logging can be enabled with:

```yaml
logger:
  logs:
    custom_components.email_ha: debug
```

Review logs before sharing them. Email HA does not log tokens or bodies, but
account identifiers and operational metadata can still be sensitive.

## Development validation

Local checks:

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pyright
```

GitHub Actions also run HACS and Hassfest validation.
