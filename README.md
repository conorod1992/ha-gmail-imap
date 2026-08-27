# Email HA

Read-only Gmail integration for Home Assistant.

Email HA brings useful Gmail information into Home Assistant without allowing Home Assistant to change or send email.

You can use it to:

- see how many unread emails you have;
- react when a new email arrives;
- watch for specific emails, such as messages from a particular sender or emails containing certain words;
- create sensors that count emails matching your own filters;
- find emails from automations;
- optionally retrieve the readable contents of an email when an automation needs them;
- connect more than one Gmail account.

Email HA uses Gmail's IMAP connection behind the scenes, but you do not need to understand IMAP for normal use.

> [!NOTE]
> Email HA is read-only. It can search and read email, but it cannot send, reply, forward, delete, move, label, star, flag, or mark messages read or unread.

## What you get

Email HA can create:

- **Primary unread** and other Gmail category sensors;
- **Latest email**, showing the newest email's basic details;
- a **New email** event you can use in automations;
- **Custom email sensors** that count matching emails;
- **Email watches** that react only when a newly arriving email matches your filters;
- a **Find emails** action with normal fields such as sender, subject, read state, category, and date;
- a **Get email contents** action for explicitly retrieving readable email content;
- an advanced raw IMAP search action;
- separate entities and events for multiple Gmail accounts.

For most users, the recommended first setup is:

- **Primary unread**
- **Latest email**
- **New email**

You can enable or disable the built-in Gmail entities later from **Configure > Gmail sensors**.

---

# Installation

## HACS custom repository

If you already use HACS:

1. Open **HACS**.
2. Open **Integrations**.
3. Open the HACS menu and choose **Custom repositories**.
4. Add:

   ```text
   https://github.com/conorod1992/ha-gmail-imap
   ```

5. Choose **Integration** as the repository type.
6. Install **Email HA**.
7. Restart Home Assistant.

Installing the repository through HACS downloads the integration, but you still need to add and configure it in Home Assistant.

After the restart, continue with **Connect your Gmail account** below.

## Manual installation

Copy:

```text
custom_components/email_ha
```

into:

```text
<home-assistant-config>/custom_components/email_ha
```

Then restart Home Assistant.

---

# Connect your Gmail account

Google requires each Email HA installation to have its own Google sign-in credentials.

You only need to set this up once for a Google Cloud project, and the same credentials can be reused if you later add another Gmail account.

You will do three things:

1. create or choose a Google Cloud project;
2. create Google OAuth sign-in credentials for Email HA;
3. add those credentials to Home Assistant.

You do **not** need to pay for Google Cloud, and you do **not** need to enable the Gmail REST API.

## 1. Create or choose a Google Cloud project

Open the [Google Cloud Console](https://console.cloud.google.com/) and create a new project, or choose an existing project you are happy to use for Email HA.

## 2. Configure Google sign-in

Open **Google Auth Platform** in Google Cloud.

If Google asks you to configure the app first:

1. Give the app a name, for example **Email HA**.
2. For a personal Gmail account, choose an **External** audience.
3. Add the Gmail account you intend to connect under **Test users** while the app remains in testing.

## 3. Add the Gmail mailbox permission

Under the Google OAuth data-access or scopes section, add this scope:

```text
https://mail.google.com/
```

Google uses this broad mailbox scope for Gmail IMAP OAuth access.

Although the Google permission itself is broad, Email HA only implements read-only email operations.

## 4. Create an OAuth client

Open:

**APIs & Services > Credentials**

Create an **OAuth client ID** and choose:

**Web application**

Add this authorized redirect URI exactly:

```text
https://my.home-assistant.io/redirect/oauth
```

Google will then provide:

- a **Client ID**
- a **Client secret**

Keep both available for the next step.

## 5. Add the credentials to Home Assistant

In Home Assistant, open:

**Settings > Devices & services > Application credentials**

Choose **Add application credentials**.

Select **Email HA**, then paste in the Google:

- Client ID
- Client secret

## 6. Add Email HA

Now open:

**Settings > Devices & services > Add integration**

Search for:

**Email HA**

Select it, enter your Gmail address, and continue to Google sign-in.

After Google approves access, Email HA will ask which Gmail entities you want to enable.

Recommended entities are already selected.

> [!TIP]
> If Google rejects access while your OAuth app is still in testing, make sure the Gmail address you are signing in with has been added under **Test users** in Google Auth Platform.

## Do I need to enable the Gmail API?

No.

Email HA does not use the Gmail REST API. It connects directly to Gmail's IMAP service using OAuth2.

## Do I need to enable IMAP in Gmail?

For personal Gmail accounts, Google states that IMAP has been always enabled since January 2025.

Google Workspace administrators can still restrict IMAP for managed accounts, so a work or organisation account may require its administrator to allow IMAP access.

See:

- [Google Gmail client guidance](https://support.google.com/mail/answer/7126229)
- [Google Workspace administrator guidance](https://support.google.com/a/answer/9003945)

---

# First setup

After Google sign-in, Email HA shows a list of Gmail entities.

The recommended entities are already selected:

- **Primary unread** — unread emails Gmail classifies as Primary
- **Latest email** — the newest email's subject, sender, date, and other basic details
- **New email** — an event entity that can trigger Home Assistant automations

Select any additional Gmail entities you already know you want, then finish setup.

The Gmail account appears in Home Assistant as one device named similar to:

```text
Gmail - you@example.com
```

New mail normally appears near real time.

Email HA keeps Gmail's normal push-style IMAP connection open and also performs an internal 15-minute refresh as a fallback if an update is missed.

There is no poll interval you need to configure.

---

# Which feature should I use?

Email HA provides several ways to work with email.

| If you want to… | Use |
|---|---|
| See how many unread Primary emails you have | **Primary unread** |
| See details of the newest email | **Latest email** |
| Run an automation whenever any new email arrives | **New email** |
| Count emails matching your own filters | **Custom email sensor** |
| Run an automation only when a matching email arrives | **Email watch** |
| Search your mailbox from an automation | **Find emails** |
| Read the contents of a specific email | **Get email contents** |
| Write a raw IMAP search query | **Advanced: Search using IMAP query** |

A useful distinction is:

- a **Custom email sensor** answers **"How many emails match right now?"**
- an **Email watch** answers **"Did a matching email just arrive?"**

---

# Gmail sensors

Open:

**Settings > Devices & services > Email HA > Configure > Gmail sensors**

to choose which built-in Gmail entities are enabled.

| Entity | Meaning | Recommended |
|---|---|---:|
| Primary unread | Unread messages Gmail classifies as Primary | Yes |
| Latest email | Newest email subject, sender, date, and selected basic details | Yes |
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

## Primary unread versus Inbox unread

These two sensors can be quite different.

**Primary unread** counts unread email Gmail has placed in the **Primary** category.

**Inbox unread** counts every unread email carrying Gmail's Inbox label, including mail that may appear under:

- Promotions
- Social
- Updates
- Forums

For many users, **Primary unread** is the more useful "important unread inbox" count.

---

# Custom email sensors

Open:

**Settings > Devices & services > Email HA > Configure > Custom email sensors**

to add, edit, duplicate, or delete custom email count sensors.

A custom email sensor counts messages that match your chosen filters.

For example, you could create:

| Name | Folder | Filters |
|---|---|---|
| RSA unread | Inbox | From contains `rsa.ie`; Read state `Unread` |
| Bookings | Inbox | Subject contains `booking`; Read state `Unread` |
| Starred Primary | Inbox | Gmail category `Primary`; Starred state `Starred` |

The first form contains the most common filters, including whether an attachment is present.

Turn on **Add more filters** to use additional fields such as:

- To
- CC
- Body text
- Any text
- Attachment filename
- Date filters

Blank fields are ignored.

Choose **All conditions** when every filled filter must match, or **Any condition** when matching any one filled filter is enough.

## What does a custom sensor retrieve?

Gmail performs the search.

Email HA receives:

- the number of matching messages;
- basic details for the newest matching message.

Email HA does **not** download message bodies simply to calculate a sensor count.

A custom sensor can expose details such as:

- newest matching message ID within the folder;
- subject;
- sender name;
- sender address;
- date.

It can also record when Email HA last observed a genuinely new matching email.

That "last new match" value only tracks new messages seen while the integration is running and resets when the integration is reloaded.

Up to **20 custom email sensors** can be configured per Gmail account.

## Folders

Email HA offers discovered Gmail folders in the folder selector.

If needed, advanced users can also type an exact folder name manually, which can be useful for localised or unusual Gmail folder layouts.

---

# Email watches

Open:

**Settings > Devices & services > Email HA > Configure > Email watches**

to add, edit, duplicate, or delete watches.

An Email watch fires an event only when a **newly arriving email** matches your chosen filters.

This is usually the easiest option when you want an automation to react only to a particular type of email.

For example:

- emails from a particular company;
- booking emails;
- emails containing a certain subject;
- emails with attachments;
- unread Primary emails from a certain sender.

Each Email watch gets its own event entity in Home Assistant.

Up to **20 Email watches** can be configured per Gmail account.

## Example: RSA email watch

Create a watch named:

**RSA emails**

with filters such as:

- **From contains:** `rsa.ie`
- **Subject contains:** `check test`

Then use that watch's event entity as the trigger in an automation.

In the Home Assistant automation editor:

1. add an **Event received** trigger;
2. choose the Email HA event entity for **RSA emails**;
3. choose event type **new_matching_email**;
4. add whatever action you want, such as a mobile notification.

Equivalent YAML:

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

Select your real entity in the UI rather than copying the example entity ID.

## Example: booking watch with an attachment

A watch named **New booking** could use:

- **Subject contains:** `booking`
- **Attachment filename contains:** `pdf`

Then react to that event:

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

Email watch events include basic email details such as:

- account;
- folder;
- message identifier;
- subject;
- sender name;
- sender address;
- date;
- watch name.

They do **not** include the email body or your private filter values.

Existing matching emails are not replayed when:

- Home Assistant starts;
- Email HA reloads;
- a new watch is created.

Only newly detected arrivals fire the watch.

---

# Automations

Email HA is designed to work with Home Assistant's normal automation editor.

The YAML below is mainly provided as a reference or for users who prefer editing automations in YAML.

## Notify when any new email arrives

In the automation editor:

1. add an **Event received** trigger;
2. select the Gmail account's **New email** entity;
3. choose event type **new_email**;
4. add a notification or other action.

Equivalent YAML:

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

Select your real entity in Home Assistant rather than copying the example entity ID.

The New email event provides details such as:

- account;
- folder;
- message identifier;
- subject;
- sender name;
- sender address;
- date.

It never includes the email body.

## Notify only for a particular sender

For most cases, creating an **Email watch** is easier than adding template conditions to the general New email event.

If you do want to filter the New email event directly, you can use a condition such as:

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

## Run when Primary unread becomes greater than zero

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

---

# Actions

Email HA provides three mailbox actions.

For most users:

1. use **Find emails** to search;
2. use **Get email contents** only if you actually need the body;
3. use **Advanced: Search using IMAP query** only if you already understand IMAP search syntax.

## Find emails

`email_ha.find_emails` is the normal search action.

It provides standard fields such as:

- From
- To
- CC
- Subject
- Body
- Any text
- Read state
- Starred state
- Importance
- Gmail category
- Date
- Attachment presence
- Attachment filename

Blank filters are ignored.

Use **All conditions** for AND matching, or choose **Any condition** to match when at least one filled filter is true. `match_mode` defaults to `all`, so existing automations keep their current behaviour.

Search results are returned newest first.

By default, Find emails returns metadata only and does not retrieve message bodies.

## Example: find unread RSA renewal emails

Home Assistant actions can save their result into a **response variable**, which lets later steps in the same automation use that result.

In this example, the search results are stored as `found_mail`:

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

## Attachment filters

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

Gmail performs the filename search, so Gmail decides how text such as `pdf` matches attachment names.

## Including email bodies in Find emails

Set:

```yaml
include_body: true
```

only when the automation actually needs readable email content.

The readable body limit is configurable from **1 to 20,000 characters per message**.

Find emails returns at most **25 messages**.

When readable content is requested, results can also include attachment metadata such as:

- whether attachments exist;
- attachment count;
- bounded filenames;
- content types.

Attachment file contents are never exposed.

---

# Get email contents

`email_ha.get_email_contents` retrieves one specific email using the folder and message ID returned by:

- **Find emails**;
- the **New email** event;
- an **Email watch** event.

It returns a readable text version of the email, limited to the configured maximum length.

Example:

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

This is a more advanced automation example because it passes the result of one action into another.

---

# Advanced: Search using IMAP query

`email_ha.search_emails` accepts raw standard IMAP SEARCH criteria.

Most users should use **Find emails** instead.

Example:

```yaml
action: email_ha.search_emails
data:
  folder: INBOX
  search_criteria: 'UNSEEN FROM "person@example.com"'
  max_results: 5
response_variable: advanced_results
```

This action is intended for users who already understand IMAP search syntax.

Gmail web-search syntax is not the same as standard IMAP syntax.

Gmail-specific raw searches require Gmail's documented `X-GM-RAW` extension.

---

# Multiple Gmail accounts

Add Email HA once for each Gmail address you want to connect.

The same Google OAuth client can be reused for multiple accounts.

Each Gmail account has its own:

- Home Assistant device;
- Gmail sensors;
- Latest email entity;
- New email event;
- Custom email sensors;
- Email watches;
- folder tracking.

Events never cross between accounts.

If only one Gmail account is loaded, Email HA actions can use it automatically.

If more than one account is loaded, select **Account** in the action UI so Home Assistant knows which mailbox to use.

---

# Privacy and security

## Plain-language summary

Email HA is deliberately read-only.

It does not provide actions that can:

- send email;
- reply;
- forward;
- delete;
- move;
- label;
- star;
- flag;
- mark email read;
- mark email unread.

Normal count sensors and New email / Email watch events do not retrieve message bodies.

An automation must explicitly request readable email contents when it needs them.

Be careful when passing retrieved email content into:

- notifications;
- logs;
- conversation agents;
- other automations;

because email content can contain private information.

## Technical security details

- OAuth2 tokens are managed by Home Assistant's application-credentials system.
- Gmail IMAP requires Google's broad `https://mail.google.com/` OAuth scope.
- Gmail does not offer a narrower read-only scope specifically for IMAP.
- Email HA enforces read-only behaviour by only implementing read-only mailbox operations.
- Gmail traffic uses TLS and XOAUTH2.
- Folders are opened read-only using IMAP `EXAMINE`.
- Message fetches use `BODY.PEEK`, so retrieving content does not intentionally mark mail as read.
- Count sensors use server-side searches and do not fetch bodies.
- Find emails and advanced search omit bodies by default.
- Explicit body retrieval is limited to 20,000 readable characters.
- Source message data is capped at 2 MB before parsing.
- HTML is converted locally to readable text.
- Scripts, remote images, tracking pixels, styles, and links are not loaded.
- Attachment metadata can be returned, but attachment file bytes are never downloaded or exposed.

---

# Advanced account settings

Open:

**Settings > Devices & services > Email HA > Configure > Advanced account settings**

to change the folder used by:

- **Latest email**
- **New email**

Inbox is recommended.

The built-in Gmail count sensors keep their documented Inbox meaning regardless of this setting.

---

# Technical details

You do not need this section for normal setup or use.

## How new mail is detected

Email HA normally receives new-mail updates through Gmail IMAP IDLE.

A fixed internal 15-minute refresh acts as a fallback if an update is missed.

Email HA therefore does not expose a user-configurable polling interval.

## How Email HA avoids replaying old mail

Gmail assigns message UIDs within each folder.

Email HA records Gmail's folder tracking values when it starts, then only treats later UIDs as new arrivals.

This prevents situations such as deleting or moving the newest email from causing an older message to be mistaken for a new arrival.

Existing messages are not emitted when:

- Home Assistant starts;
- the integration reloads;
- a new Email watch is created.

Multiple newly detected messages are emitted in arrival order.

To keep processing bounded, one refresh handles at most the newest **25 arrivals** before advancing the folder baseline.

Large skipped bursts are logged rather than replayed indefinitely.

## UIDVALIDITY and UIDNEXT

For users familiar with IMAP:

- Email HA tracks Gmail `UIDVALIDITY`;
- it uses `UIDNEXT` to establish a clean baseline;
- UIDs are only meaningful within one folder and one UIDVALIDITY generation;
- reconnecting IMAP IDLE keeps the existing baseline;
- reloading the integration creates a new baseline;
- copying or moving a message to another folder gives it folder-specific identity.

## Gmail-specific search behaviour

Gmail category and importance counts use Gmail's documented `X-GM-RAW` IMAP extension.

Starred state uses the standard IMAP `\Flagged` state.

Google documents Gmail-specific IMAP search behaviour here:

[Google Gmail IMAP extensions](https://developers.google.com/workspace/gmail/imap/imap-extensions)

---

# Troubleshooting

## Email HA does not appear when I search for integrations

Check that:

1. Email HA is installed in HACS;
2. Home Assistant has been restarted since installation;
3. your browser has been refreshed.

Remember that installing through HACS downloads the integration, but you must still add it from:

**Settings > Devices & services > Add integration**

## Home Assistant says application credentials are missing

Complete the Google OAuth setup first, then add the credentials under:

**Settings > Devices & services > Application credentials**

Select **Email HA** and enter the Google Client ID and Client secret.

## Google rejected access

Check:

- the OAuth consent screen is configured;
- the Gmail account is listed under **Test users** while the app is in testing;
- the scope is exactly:

  ```text
  https://mail.google.com/
  ```

- the OAuth client type is **Web application**;
- the redirect URI is exactly:

  ```text
  https://my.home-assistant.io/redirect/oauth
  ```

- the Client ID and Client secret were copied correctly.

## Google says the app is unverified or blocked

For a personal OAuth project that is still in testing, make sure the Gmail account you are signing in with is listed under **Test users**.

## Home Assistant reports an OAuth callback problem

A callback failure usually means Home Assistant does not have a usable external URL for returning the browser to your instance.

Check your Home Assistant external URL configuration and My Home Assistant redirect setup.

## A managed Google Workspace account cannot connect

Ask the Google Workspace administrator whether:

- IMAP is allowed;
- the OAuth app is allowed.

Workspace administrators can restrict mailbox access even though personal Gmail accounts have IMAP enabled automatically.

## I installed Email HA but do not see all the sensors

Only the recommended entities are enabled by default.

Open:

**Settings > Devices & services > Email HA > Configure > Gmail sensors**

to enable additional Gmail entities.

## An Email watch did not fire for an existing email

This is expected.

Email watches only react to newly detected arrivals.

Existing matching messages are deliberately not replayed when:

- Home Assistant starts;
- Email HA reloads;
- a watch is created.

## A folder is unavailable

Use a discovered folder from the folder selector where possible.

Advanced users can also enter the exact IMAP folder name manually.

## A new email did not appear immediately

Gmail IMAP IDLE is the normal update path and is usually near real time.

Email HA also performs an internal refresh every 15 minutes as a resilience fallback.

---

# Debug logging

Temporary debug logging can be enabled with:

```yaml
logger:
  logs:
    custom_components.email_ha: debug
```

Review logs before sharing them.

Email HA does not intentionally log OAuth tokens or email bodies, but account identifiers and operational metadata can still be sensitive.

---

# Development validation

Local checks:

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pyright
```

GitHub Actions also run HACS and Hassfest validation.
