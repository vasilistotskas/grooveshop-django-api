# Emails: sending, gates, the store's switch and opt-out

## `send_email(type_key, obj, *, recipient=None, attachments=())`

Returns one `Delivery(type_key, status, reason, recipient, subject)` per
recipient (`subject` is the one that went out, for `SENT`), or a single
skip. `was_sent(deliveries)` says whether any recipient was sent. In order:

1. The type is looked up (`UnknownEmailType` if absent).
2. **Owner.** A `platform` type runs under `schema_context(public)`. A
   `store` type needs a bound store and raises `StoreRequired` without one:
   there is no fall back to the platform.
3. **Gates**, in the order the type lists them. The first closed one gives
   `SKIPPED_FEATURE_OFF` with its label as `reason`.
4. **Switch.** A switchable type the store turned off gives
   `SKIPPED_SWITCHED_OFF`. A required type never reads the switch.
5. **Recipients**: `recipient`, else `email_type.recipients(obj)`. None
   gives `SKIPPED_NO_RECIPIENT`.
6. Per recipient: **opt-out** (marketing only, `SKIPPED_OPTED_OUT`), the
   **suppression list** (`SKIPPED_SUPPRESSED`; a required mail that is blocked
   is logged as a warning), then
   `render` in `recipient.language`, the `EmailLog` row (`QUEUED`),
   `build_headers`, and `transport.deliver`; the row becomes `SENT` (with
   the transport's `Receipt`) or `FAILED`.

A transport failure is logged (no address in the line) and re-raised, so the
calling task's own `autoretry_for=MAIL_TRANSIENT_ERRORS` applies. The
caller keeps its idempotency claim and its bookkeeping: the pipeline does
not take over business de-duplication. A task that reports sent or not
maps the `Delivery` list itself (`was_sent`): a skip is a deliberate no-op,
not a failure.

### Attachments

`attachments` is a sequence of `emails.transport.Attachment(filename,
content, mimetype)`, attached to the message of every recipient after the
HTML alternative. The `content` bytes are `repr=False` and nothing logs them.
The caller reads the file (a receipt PDF) before calling, so a send that is
skipped still reads it.

Every outcome is also a row of `EmailLog` (see The email log in [`delivery.md`](delivery.md)); a skip is a
row with its status and `reason` and no recipient work, and is still a
structured log line (`email_type`, `status`, `reason`).

### Language

The language is the recipient's. The resolvers state it: `customer_of`
(an `Order` or a `UserAccount`: its own `language_code`, else the account's,
else the store's), `store_operators` (the store's), `platform_operators`
(`settings.LANGUAGE_CODE`). `store_operators` is the bound tenant's
`contact_email` then `owner_email` and nothing else: a store with neither
has nobody to write to, and `settings.ADMINS` is never used for it. When
both exist and differ, each gets its own mail.

### Sender

`From` is `emails.sender.from_address()` read in the schema the
send runs under, so a platform type is sent as `"{SITE_NAME}"
<DEFAULT_FROM_EMAIL>` and a store type as the store (its own verified domain,
else its name on the platform's sender). `Reply-To` (`sender.reply_to(email_type, obj)`) is what the type names
(`EmailType.reply_to`), else the store's contact
address for a store type sent to customers, and empty for a platform type
and for mail to the store's own staff.

### Colours

`core.utils.email_context.build_email_context` puts the store's colours in
the context as `THEME`, resolved by `tenant.credentials.tenant_email_theme()`
to literal hex that `email_base.html` writes into its `<style>` block: mail
clients drop the `:root { --x: ... }` declaration, so CSS custom properties
cannot carry a colour.

The source is the store's theme document in its CURRENT release, the same
one the storefront draws its look from, not any `Tenant` column. The mail
wears `colors.light.primary.scale` shade 600 (buttons, links, the header) and
shade 700 (a button's hover). Nothing else of the theme is read: the accent
and the status colours are stored as the colour a store paints, and what an
unchanged store paints depends on its storefront design, which the API does
not know.

The platform palette (`_DEFAULT_EMAIL_THEME`) is used when

- the mail renders outside a store's schema (every `platform.*` type renders
  under `public`; a task with no tenant has none);
- the store has no release, or its release has no theme document;
- the primary scale is the platform's default scale (the one a new store
  starts with, read from the catalogue pinned with the seeds): a store has a
  scale whether or not its merchant chose one, so only a scale that differs
  is a brand;
- the document cannot be read or shade 600 or 700 is not `#RRGGBB`. This is
  logged as a warning (not for the first three, which are normal) and never
  stops the mail.

The read is `builder.published.published_index` for the default language,
the cached chrome read the storefront's own requests use, so a warm release
costs no query and mail adds no cache of its own.

## Gates (`gates.py`)

Each gate has a `label` (the reason a store reads, "Gift cards feature")
and `is_open()`. All are frozen, keyword-only dataclasses.

| Gate | Open while |
|---|---|
| `FeatureGate(feature=...)` | `tenant.features.feature_on(feature)`; the name must be in `STORE_FEATURES` (checked when the gate is built). |
| `StoreSwitchGate(setting=...)` | a server-side store setting is on (`MYDATA_ENABLED`); the name must be declared in `EXTRA_SETTINGS_DEFAULTS`. |
| `CarrierGate(carrier=...)` | the `ShippingProvider` with that code is active. |
| `ScheduleGate(entry=...)` | the `scheduler.PeriodicTask` of that name is enabled (read in `public`) and the entry is declared in `CELERY_BEAT_SCHEDULE` (a row a deploy removed stays until beat restarts). |

## The store's switch

`EmailSwitch` is sparse: one row per type a store turned **off**. A type
with no row is on, so a new type needs no data migration. Rows are written
only through `emails.services.set_switch(user, type_key, enabled=...)`:
it needs `emails.change_emailswitch`, and `EmailSwitch.clean` refuses a
required type (`email_type_required`) and an unknown one
(`unknown_email_type`). Switching a type on removes the row. Both changes
are in simple-history with the staff member.

The list of switched-off keys is cached as
`emails:switches:<tenant.cache_generation>`; a write moves the generation
(`tenant.signals.bump_generation_on_email_switch_change`), so the next
request or task sees it. A request that already holds the store row keeps
the old list until it ends. The public schema has no generation and reads
the table directly.

### The store's email pages (`src/emails/admin.py`, `src/emails/switches.py`, `src/emails/figures.py`)

`Settings -> Emails` in the store admin. A dedicated page, like the text
editor, not a changelist: `EmailSwitchAdmin.changelist_view` is the **status
page** and `type_view` (`type/<key>/`) the page of one email.

**Status page.** Two tabs by `EmailType.audience` ("To customers", "To your
team"), a text search over key, title and description, and a state filter (All,
On, Off, Failing), all GET parameters so a filtered view is a URL. Rows are
grouped by `EmailType.area` (`switches.AREA_TITLES`, one title per area;
`tests/unit/emails/test_switches.py` fails when a type is declared in an area
with none). Each row has a badge (**On**, **Required** with no switch, **Off:
<gate label>**, **Switched off**), what it sent in the last 30 days (a
count, and the time of the last send), a **failed** count linking to the log
filtered to that email, the switch, **Preview** (its page) and **Send test**.
The figures are one aggregate over `EmailLog` for the whole store
(`figures.type_figures`, grouped by type, `@dashboard_query("emails:figures:v1",
ttl=60)` with no `depends_on`, because log writes never stop), so the page
costs the same queries for 2 types and for 30
(`tests/integration/emails/test_email_pages.py`). A test send is not counted.
A switch posts to `toggle/` (htmx), which calls `set_switch` and answers with
the row; a required or unknown type is a 400 or 404 and writes nothing. The
page reads `EmailSwitch` directly, not the pipeline's cached list, so it shows
what was just written. The page never shows an address.

**One email.** The preview (below), **Send test**, and its latest 20 sends with
the masked recipient, the status and what the provider reported (the
`EmailEvent` timeline), in a table that scrolls sideways on a phone.

**Send test** (`src/emails/testsend.py`, `test_send_view`) mails the email to the
signed-in staff member's own address and to nobody else (the request's `to`,
`email` or `address` fields are never read), in the chosen language and from
the sample or a chosen order. It skips the gates, the switch, the opt-out and
the suppression list, so a switched-off email can be checked, goes through the
real transport and leaves an `EmailLog` row with `is_test` and
`triggered_by_staff`; the subject is prefixed `[Test]`; no business flag is
written. Each staff member may send `EMAIL_TEST_SENDS_PER_HOUR` (10) per hour
(counted per attempt, in the cache).

**Log** (`EmailLogAdmin`): read-only, filters by email, status, "Failing" (7 or
30 days) and date; the search box takes an **exact address**, hashed here and
matched on `recipient_hash` (any other text searches the subject), so the admin
never holds or shows an address. **Suppression list** (`EmailSuppressionAdmin`):
read-only except removing a row, behind the standard delete confirmation whose
dialog says the address must also be removed in Resend's dashboard (Resend
keeps its own list; no API removes an address); the admin log names the actor.

Permissions (`src/tenant/role_scopes.py`): `emails` is in `STORE_SETTINGS_APP_LABELS`,
so ADMIN and OWNER hold every `emails.*` permission (switch, send a test, remove a
suppression); it is also in `STAFF_READ_ONLY_APP_LABELS`, so STAFF only **look**
(the page, the log and the suppression list, with the switch disabled and no
Send test). The audit of a switch is the simple-history row with the staff member
(`EmailSwitch.history`). Platform types (`Owner.PLATFORM`) are not on these pages.

**The control plane's page** (`PlatformEmailSwitchAdmin`, `Settings`-like
entry "Emails" in the platform sidebar's Platform group, registered because
`emails` is in `PLATFORM_APP_LABELS`). It is the same admin over the `public`
schema: the `Owner.PLATFORM` types, grouped by who receives them ("To stores",
"To the platform's operators") instead of by area and tabs, with the same
badge ("Required": every platform type is required and ungated, so nothing
can be switched and the toggle answers 404), figures, filter (All, Failing),
preview, Send test, log and suppression list. Three things differ because the
schema does:

- **Which emails.** `EmailSwitchAdmin.owner` is `Owner.STORE` and the
  platform subclass's is `Owner.PLATFORM`: it decides which types the page and
  a type's page serve (a store type's page on the platform, and the reverse, is
  a 404), and `switches.switch_groups(owner=...)` groups them. The log and the
  suppression list are one admin class on both sites; `serving_owner()` (the
  schema in use) picks the email filter's choices.
- **No orders.** A platform email is about no order and the orders table is not
  in `public`: the preview form has no order field and a posted order id is not
  read (`_may_pick_orders`).
- **Where a test is sent from.** `testsend.send_test` runs under
  `owner_schema(email_type)`, as `send_email` does, so a platform email's test is
  rendered, logged (`is_test`) and sent from `public` whatever schema the request
  is in. The operator receives it at their own address, throttled per operator.

Every admin URL the pages build is looked up on `self.admin_site.name`
(`admin` or `platform_admin`), and the templates take theirs from the view,
because a fixed `admin:` namespace would point the platform at the store's.
The figures are cached under a key that carries the schema
(`tenant.cache.make_tenant_key`), so the platform's page and a store's never
share an answer (`tests_mt/test_platform_email_pages.py`). The platform
dashboard flags the platform's own mail that failed in the last 7 days
(`PlatformAlerts`, from the `public` log) next to the "no Resend key" alert.

**Dashboard.** "Emails failing" in the store dashboard's Needs attention card
counts the last 7 days of failed, bounced, complained and provider-suppressed
rows (`figures.failing_since`, in `attention_counts`), and opens the log under
the same filter.

### The preview (`src/emails/preview.py`)

`EmailSwitchAdmin.type_view` embeds a panel that an htmx form fills
(`preview/render/`, POST only): a language, the data (sample data, or one of
the store's ten most recent orders) and HTML or text. HTML is shown in a
sandboxed `srcdoc` frame, text in a `pre`. A viewer needs the admin site's own
tenant-membership gate and `emails.view_emailswitch`; the choice of a real order
is offered, and a posted order id honoured, only with `order.view_order`,
because an order carries a customer's name and address; `is_staff` alone is never
enough.

`build_preview` calls `emails.render.render`, the function `send_email`
calls, so the preview is the mail that is sent (`test_preview_matches_send`
sends each order email and compares): from a chosen order for the order types
(`order.emails.ORDER_EMAIL_KEYS`, found through `preview_object`), else from
the type's own `sample`. An order that does not exist, or that has no record
the email is about (no refund, no receipt, no shipment), is reported and never
replaced by sample data.

## Opt-out (`optout.py`)

Only marketing types honour an opt-out; a receipt or a security mail is not
something a person can decline.

- `EmailOptOut(address_hash, type_key, source)` is unique per
  `(address_hash, type_key)`. `type_key` is a marketing type's key, or `*`
  for every marketing type.
- `address_hash(address)` is HMAC-SHA256 of the stripped, lower-cased
  address under `settings.EMAIL_HASH_KEY`: the table holds no address.
- `make_token(address, type_key)` signs `{schema, h, t}` (plus `a`, the
  account pk, when the address belongs to an account: the hash cannot be
  turned back into an account and the address must not travel in a URL) with
  `django.core.signing` under the salt `emails.optout`. It names the schema
  because `SECRET_KEY` is global: a token from one store does not verify on
  another. `read_token` / `opt_out_by_token` raise `InvalidToken` for a bad
  signature, an expired token (365 days), another store, an unknown type or
  a type that takes no opt-out.
- `unsubscribe_url` builds `<api host>/api/v1/emails/unsubscribe/<token>`
  (`UNSUBSCRIBE_PATH`); `EmailUnsubscribeView` serves it (below). It is the
  URL of the `List-Unsubscribe` header, for mailbox providers.
- `confirm_url(address, email_type, *, language)` builds the storefront page
  a link in a mail BODY opens: `<storefront>[/<locale>]/email/unsubscribe/<token>`
  (`CONFIRM_PATH`, through `get_tenant_frontend_url`), carrying the same
  token. The page shows a button that POSTs the token to the endpoint
  (through the storefront proxy). `confirm_url_for_token` is the same URL for
  a token already in hand. A body link never points at the API URL: a link
  scanner would follow it.

### The unsubscribe endpoint

`POST /api/v1/emails/unsubscribe/<token>` is the RFC 8058 one-click target
named by `List-Unsubscribe` and flagged by `List-Unsubscribe-Post`.

- It records `EmailOptOut` (source `one_click`) for the token's type, or for
  every marketing type when the token carries `*`, and answers `200` with
  no body. It is idempotent: the row is created once.
- A forged, tampered, expired or other-store token, an unknown type and a
  type that takes no opt-out all answer the same `400`
  (`code: unsubscribe_token_invalid`). The reason goes to the log only. A
  token minted in one store's schema is a `400` on every other host.
- No authentication and no CSRF check (a mailbox provider sends neither);
  the POST body is not read, so the `List-Unsubscribe=One-Click` form, in
  any encoding, is accepted. Throttled per client address by
  `EmailUnsubscribeThrottle` (`email_unsubscribe`, fail open).
- **A `GET` never opts anyone out.** A link scanner follows every URL in a
  mail, and some mail clients open the `List-Unsubscribe` URL with a GET, so
  it answers `302` to the storefront confirm page (`confirm_url_for_token`,
  in the store's default language: a header GET carries none) without
  reading the token. An invalid token shows on the page, from the POST's
  `400`. Throttled like the POST.
- The type-scoped POST stays type-only: the confirm page's main button and a
  mailbox provider's one-click both use it and are recorded as `one_click`.
- `POST /api/v1/emails/unsubscribe/<token>/all`
  (`EmailUnsubscribeAllView`, operation `unsubscribeFromAllMarketingEmail`) is
  the page's second button, "stop all marketing emails from the store". Same
  auth, throttle, `400 unsubscribe_token_invalid` and bodyless idempotent
  `200`; any valid token of the store will do, whatever its type. Source
  `link`. `user.services.subscription.unsubscribe_from_all_marketing` writes,
  in one transaction: `EmailOptOut("*")` and, for an account (the token's
  `a`), `marketing_opted_out_at` and the end of its ACTIVE topic subscriptions,
  all through `opt_out_of_marketing`, the one writer (it takes the address
  HASH, which is all a token holds). A guest's token (no `a`) records the
  `EmailOptOut` row only.

### `EMAIL_HASH_KEY`

A required environment variable in production **and staging** (any
non-dev, non-CI deployment: the process refuses to start without it).
Development and CI get a fixed insecure key. Generate one
with `secrets.token_urlsafe(48)` and keep it with the other platform
secrets. It is separate from `SECRET_KEY` on purpose: rotating `SECRET_KEY`
must not orphan the opt-outs. Changing `EMAIL_HASH_KEY` forgets every
opt-out (the stored hashes stop matching, and outstanding unsubscribe links
stop working), so it is never rotated casually.

## Headers (`headers.py`)

`build_headers(email_type, recipient)` decides by audience, then by kind.
Only `customer` mail is list mail; `store_staff` and `platform_operator`
mail gets no list headers (`{}`). For customers:

- **Transactional**: `List-ID: <{list_id}.{store domain}>` and, when the
  store has a contact address, `List-Unsubscribe: <mailto:{contact}?subject=unsubscribe>`.
  Never `List-Unsubscribe-Post`: there is no programmatic unsubscribe for
  a receipt.
- **Marketing**: `List-Unsubscribe` with the `mailto:` form (when the store
  has a contact address) and the https unsubscribe URL,
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` and `List-ID`
  (RFC 2369, RFC 8058, Google's bulk-sender rules).

`build_list_unsubscribe_headers(url, *, list_id)` and
`build_transactional_list_headers(list_id=...)` live in `headers.py`, under
`build_headers`; nothing else calls them. The one-click URL is the
pipeline's own (`/api/v1/emails/unsubscribe/<token>`). The re-engagement
body links to the confirm page of the same type (`optout.confirm_url`), and
the product-alert bodies carry no unsubscribe link.

## Header safety

A subject is the only header built from data a store writes (a product or
company name). `render.clean_header_text` turns every whitespace run
(carriage return and line feed included) into one space, drops control
characters and caps the length at 200, so a name containing `\r\nBcc: x`
cannot add a header. `transport` relies on Django's own refusal as the
backstop: an address with a line break raises `BadHeaderError`. Data-derived
subject pieces reach the subject through the context builder, never by
concatenation at a call site.

## allauth mail

allauth builds each of its mails and calls
`user.adapter.UserAccountAdapter.send_mail(template_prefix, email, context)`.
The adapter looks the prefix up in `user.emails.ALLAUTH_TYPES` (a prefix it
does not know raises `KeyError`, so a mail a later allauth adds never goes
out unbranded) and queues `user.tasks.send_allauth_email_task`; the task calls
`send_email`, so the mail is rendered, headed and sent like every other. The
sending is in a task because allauth's own `send_mail` talks SMTP inside the
view, where a 421 from the mail server would fail the sign-up request; in the
task it is a retry.

The 20 types are `account.*` (thirteen), `mfa.*` (five) and `social.*` (two),
declared in `src/user/emails.py` through `_allauth_email`, which also fills the
prefix map. All are customer mail, transactional and **required**: a store
cannot switch a sign-in code, a reset link or a security notice off. Their
templates are `src/core/templates/emails/{account,mfa,social}/<name>.{html,txt}`;
the security notices share `emails/base/security_notice.{html,txt}` and the
plain-text footer `emails/base/account_message.txt`. The two sign-up
confirmations share one template pair (`email_confirmation`).

What crosses the queue is `AllauthMail`: the address, the language, the
account's pk and the context keys the templates read
(`ALLAUTH_CONTEXT_KEYS`), as text. The request, the account row and the
reset key are dropped; a timestamp travels as ISO text. The language is the
account's when allauth names one, else the visitor's (an unknown address, a
notice, a login code). The builder gives the storefront links allauth
resolved from `HEADLESS_FRONTEND_URLS` that language
(`localize_storefront_url`). The task's `context` is a sensitive kwarg: a
reset link or a code never reaches the failure log. An account deleted before
the task runs gets no mail.

The subject is the type's msgid, as everywhere on the pipeline, with no
store-name prefix (the `From` carries the store's name).
