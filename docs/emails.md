# Emails

The email pipeline: one catalogue of email types, one function that sends
them, and the switch and opt-out a store or a recipient uses to stop one.
Source: `emails/` (`types.py`, `registry.py`, `gates.py`, `recipients.py`,
`sender.py`, `headers.py`, `optout.py`, `render.py`, `send.py`,
`transport.py`, `models.py`, `services.py`), `tests/utils/emails.py` and
`tests/utils/golden.py` (the test helpers). The code wins over this page.

**State.** The order, shipping, product, gift card, B2B, contact, account,
cart and operational alert emails are on the pipeline (see Registered
types). What is not yet (allauth's account, MFA and social mail, the
platform's own mail) still goes through its own send path and moves onto it
one domain at a time, each move keeping the recorded golden files equal
(see Tests).

## The pieces

| Module | What it does |
|---|---|
| `types.py` | `EmailType`, the contract of one kind of email, and `Audience`, `Kind`, `Owner`, `RenderEnv`. |
| `declare.py` | `customer_email`, `marketing_email`, `staff_email`: the three shapes a domain declares its types with. |
| `registry.py` | `registry`: `register`, `freeze`, `get`, `all_types`, `types_for`. |
| `gates.py` | The four gates a type can list. |
| `recipients.py` | `Recipient(address, language)` and the resolvers `customer_of`, `store_operators`, `platform_operators`. |
| `sender.py` | The `From` and `Reply-To`. Reuses `tenant.credentials`. |
| `headers.py` | `build_headers`: the list headers by kind, and the two builders under it. |
| `optout.py` | The address hash, `is_opted_out`, `opt_out`, the signed unsubscribe token. |
| `views.py`, `urls.py` | `EmailUnsubscribeView`, the RFC 8058 one-click target. |
| `render.py` | `render`: subject and both bodies in one language; `clean_header_text`. |
| `transport.py` | Builds the `EmailMultiAlternatives` (and attaches `Attachment`s) and sends it; the pipeline's only sender. |
| `send.py` | `send_email`: the whole envelope. |
| `models.py`, `services.py` | `EmailSwitch`, `EmailOptOut`; `set_switch`, the only writer of switches. |

`emails` is in both `SHARED_APPS` and `TENANT_APPS`: a store's switches and
opt-outs live in its schema, the platform's own in `public`. It imports no
domain app: each app declares its types in `<app>/emails.py`, next to the
data its builder reads. `EmailsConfig.ready` imports every app's `emails`
module (`autodiscover_modules`), then freezes the registry.

## An email type

`EmailType` is a frozen, keyword-only dataclass:

- `key`: `order.shipped`, lower-case words joined by dots, at most 64
  characters. It is the switch key, the opt-out key and the default
  `List-ID` (dots become dashes) unless `list_id` is given.
- `title`, `description`, `area`: what the admin will show and group by.
- `audience` (`customer`, `store_staff`, `platform_operator`), `kind`
  (`transactional`, `marketing`), `owner` (`store`, `platform`).
- `required`: legally or functionally required. Never switchable, and it
  cannot be marketing.
- `templates`: `emails/order/order_shipped`, with `.html` and `.txt` beside it.
- `subject`: a `gettext_noop` msgid with plain named fields only
  (`{order_id}`: no attribute or index access, no conversion or format spec;
  checked when the type is built). `render.format_subject` substitutes
  `str(context[name])` and evaluates nothing. A translation that is invalid
  or names another field is logged and the msgid is used, so one bad
  msgstr (.po, Rosetta overlay) cannot stop an email. The result is cleaned
  (see Header safety).
- `build_context(obj, env) -> Mapping`: the one reader of the domain, for
  the sent mail and the preview alike. Runs under the recipient's language.
- `sample(env)`: an unsaved object graph the preview renders.
- `recipients(obj) -> Sequence[Recipient]`.
- `gates`: every gate must be open.
- `reply_to(obj) -> Sequence[str]` (optional): where replies go when that is
  not the store's contact address. Only the contact and feedback alerts set
  it: a store's mail to its own staff about a submission is answered to the
  submitter.

`switchable` is `not required`; `opt_out_applies` is `kind is MARKETING`.

A type that breaks a rule is not constructed (`ImproperlyConfigured`, every
broken rule named): a bad key, a required marketing type, a platform type
for customers. The registry refuses at `register` a duplicate key, and at
`freeze` a missing `.html` or `.txt`, and a template file that sits in a
directory some type uses while no type names it. Only directories the
catalogue already uses are scanned, so the templates of mail that has not
moved onto the pipeline are none of its business.

## Registered types

Declared in `order/emails.py`, next to the builders that read the order:

| Key | About | Requirement / gates |
|---|---|---|
| `order.received`, `order.payment_confirmed` | the `Order` | required; `FeatureGate("cart")` |
| `order.payment_failed`, `order.shipped` | the `Order` | required |
| `order.delivered`, `order.completed` | the `Order` | switchable |
| `order.canceled`, `order.returned` | the `Order` | required |
| `order.status_refunded` | the `Order`, closed as REFUNDED | required |
| `order.refunded` | one refund `PaymentTransaction` | required |
| `order.receipt_issued`, `order.credit_issued` | the `Invoice` / `RetailCredit` (PDF attached) | required; `StoreSwitchGate("MYDATA_ENABLED")` |
| `shipping.acs_out_for_delivery` | the `AcsShipment` | switchable; `CarrierGate("acs")` |
| `shipping.boxnow_at_locker` | the `BoxNowShipment` | required; `CarrierGate("boxnow")` |
| `team.new_order` | the `Order` | switchable, store staff |
| `team.dispute_opened`, `team.dispute_lost` | an `OrderDispute` (order, dispute id, provider) | switchable, store staff |

Declared in `product/emails.py` (templates in `emails/product_alerts/`; the
ops alert `ops.review_pending` is under Operational alerts below):

| Key | About | Requirement / gates |
|---|---|---|
| `product.restock_alert` | a `ProductAlert` (restock) | **marketing**; `FeatureGate("product_alerts")` |
| `product.price_drop_alert` | a `PriceDrop` (the alert and the price reached) | **marketing**; `FeatureGate("product_alerts")` |
| `product.alert_confirmation` | a guest's pending `ProductAlert` (its confirmation link) | customer, transactional (it answers the request just made); `FeatureGate("product_alerts")` |
| `team.low_stock` | a `LowStock` (the products at their threshold) | switchable, store staff; `FeatureGate("catalogue")` |

Declared in `giftcard/emails.py` (`FeatureGate("gift_cards")` on all):

| Key | About | Requirement |
|---|---|---|
| `giftcard.delivery` | the `GiftCard`, to its recipient | required |
| `giftcard.purchase_receipt` | the `GiftCardPurchase`, to the buyer | required |
| `giftcard.expiry_reminder` | the `GiftCard`, to its recipient | switchable |

Declared in `b2b/emails.py` (`FeatureGate("wholesale")` on all):

| Key | About | Requirement |
|---|---|---|
| `b2b.profile_approved`, `b2b.profile_rejected` | the `BusinessProfile`, to its applicant | required |
| `team.b2b_application` | the `BusinessProfile`, to the store's staff | switchable, store staff |

Declared in `contact/emails.py` (the submitter is `Reply-To`):

| Key | About | Requirement / gates |
|---|---|---|
| `team.contact_message` | the `Contact` | switchable, store staff |
| `team.feedback` | the `Feedback` | switchable, store staff; `FeatureGate("feedback")` |

Declared in `user/emails.py` and `cart/emails.py`:

| Key | About | Requirement / gates |
|---|---|---|
| `user.data_export_ready` | the `UserDataExport` | required (GDPR) |
| `newsletter.confirmation` | the `UserSubscription` (a guest row too) | required (double opt-in); `FeatureGate("newsletter")` |
| `user.reengagement` | the `UserAccount` | **marketing**; `ScheduleGate("send-inactive-user-notifications")` |
| `cart.checkout_abandoned` | the `Cart` | **marketing**; `FeatureGate("cart")` |

### Operational alerts (`ops.*`)

Ten mails to the store's own staff, for work that needs a person. They were
`OpsAlertKind` and `send_ops_alert` (`shipping/alerts.py`); each is now an
email type declared with `staff_email(..., area="ops")`, so the store's
`EmailSwitch`, the gates and `store_operators()` apply. All are switchable,
store staff, with no list headers. A store with no contact or owner address
gets `SKIPPED_NO_RECIPIENT` (the old `ADMINS` fallback is gone with
`tenant_admin_recipients`); the work still shows on the dashboard counter or
the order note, whatever the switch says.

| Key | About | Declared in | Gate |
|---|---|---|---|
| `ops.shipment_rejected` | a `ShipmentRejected` (order id, carrier, its error, the remedy) | `shipping/emails.py` | |
| `ops.webhook_failed` | a `WebhookFailed` (carrier, message id, error) | `shipping/emails.py` | |
| `ops.stale_shipments` | a `StaleShipments` (the `AcsShipment` rows, threshold, clock) | `shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.unremitted_cod` | an `UnremittedCod` (the rows of `_unremitted_cod_rows`) | `shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.unattributable_cod_payouts` | an `UnattributablePayouts` (counts, payment date) | `shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.payment_after_cancel` | a `PaymentAfterCancel` | `order/emails.py` | |
| `ops.payment_mismatch` | a `PaymentMismatch` | `order/emails.py` | |
| `ops.refund_failed` | a `RefundFailed` | `order/emails.py` | |
| `ops.gift_card_reversal` | a `GiftCardReversal` (the purchase, and what was already spent, or none when only disputed) | `giftcard/emails.py` | none |
| `ops.review_pending` | the `ProductReview` | `product/emails.py` | `FeatureGate("product_reviews")` |

`ops.gift_card_reversal` is not behind the gift cards feature, unlike the
other gift card mail: a store that switched the feature off can still have a
sold card whose payment is reversed or disputed, and that alert must not
vanish. The two outcomes share one type, one switch and one template (the
headline is chosen in the builder, under the recipient's language).

A task treats a skip by what it protects. The stale digest claims its rows
(`stale_alert_sent`), sends, and releases the claim when nothing was sent
(switched off, nobody to write to, ACS off): the parcels are in the first
digest after the store turns it back on. The order-bound alerts
(`payment_after_cancel`, `payment_mismatch`, `refund_failed`,
`shipment_rejected`) write their `OrderHistory` note first, whatever the
switch. Every sender is best-effort: a mail failure never fails the task or
masks the error it reports.

Each store's old `OPS_ALERT_<KIND>_ENABLED` setting that was OFF became its
`EmailSwitch` row in `emails/0003_import_ops_alert_switches` (a setting still
on needs none: no row is on). The import moves the store's cache generation
(a bulk insert fires no signal). The setting rows themselves are left in
place for one release, because the previous image still reads them while a
release rolls out; a later release deletes them (`contract_of` 0003), and
until then they are dead rows in the store settings list. The declarations
and the "Operational Alerts" settings group are gone.

A marketing type is switchable, honours `EmailOptOut` and carries the
RFC 8058 headers. `EmailOptOut` is the ONE gate for every marketing type:
no task filters on `UserAccount.marketing_opted_out_at` any more. That
field stays as account state (the admin shows it, the blanket link sets
it), and `emails/0002_import_marketing_opt_outs` copied it into a `*` row
for every account that opted out before the link also wrote `EmailOptOut`
(#368). In the customer admin staff may RECORD an objection a customer made
by phone or email (setting the field writes `EmailOptOut("*")`,
`UserAdmin.save_model`); once set the field is read-only, because only the
customer can lift an opt-out, through their own preferences (below).

### Email preferences (`GET` and `PATCH /api/v1/user/email-preferences`)

A signed-in customer reads and changes the opt-outs of THEIR OWN account
address (`request.user.email`; nothing in the body names an address).
`emails.preferences.offered_types()` is the list: the store's marketing
types for customers whose gates are open and that the store has not
switched off. `GET` returns `{types: [{key, title, enabled}]}`; `enabled`
is false when an `EmailOptOut` row for the address covers the type (its key
or `*`). `PATCH {types: [{key, enabled}]}` answers with the same list. A key
that is not an offered marketing type is a `400` (`unknown_email_type`), a
repeated key a `400` (`duplicate_email_type`).

`user.services.subscription.set_marketing_preferences` is the only writer
that lifts. In one transaction, per choice: declining records a per-type row
(source `account`; nothing is added under a `*` row); accepting deletes the
type's own row and, when a `*` row covers it, replaces the `*` with one row
for every OTHER marketing type (the `*` row's source is kept), so the
customer's other choices stand. When no row is left for the address,
`marketing_opted_out_at` is cleared too; while any row remains it stays.
The blanket link and the admin still only write (`opt_out_of_marketing`).

A product alert made by a signed-in customer whose account address opted
out of its type is refused with a `409` (`email_opted_out`) instead of a
`201` that could never be delivered (`product.views.alert`).

A guest's alert is a double opt-in (`product/alerts.py`). Every guest
request answers the same `202` and a `detail`, whatever the address holds
(nothing, a live or pending alert, an opt-out), and always enqueues one
task, `request_guest_product_alert_task`, which does every
address-dependent step: it stores the alert INACTIVE and sends
`product.alert_confirmation`, unless the alert is live (and not opted out)
or a link went out in the last 10 minutes. The link opens the storefront's
`/alerts/confirm/<token>`, whose button POSTs to
`POST /api/v1/product/alert/confirm/<token>`; a GET changes nothing, as
link scanners follow GETs. The token is signed under its own salt, names
the store's schema and the alert's uuid, and lasts the newsletter's week
(`CONFIRMATION_TTL`; `410` when expired, `400` for a bad or other-store
token or an alert no longer pending). Confirming activates the alert and is
fresh consent from the address's own inbox: it lifts the address's opt-out
of THAT alert's type only, through `set_marketing_preferences` (a `*` row
is split; with no account, `account_pk=None`). Pending alerts whose link
has expired are deleted daily (`purge-unconfirmed-guest-alerts`, every
store including suspended ones).

A task treats a skip by what it protects. An alert, a delivery or a
reminder skipped because its type is OFF (gate closed, or switched off)
stays armed for when it is back on: the product alerts stay active, a gift
card stays undelivered, a low-stock claim is released. One that is settled
for good is not found again: an alert or reminder to a recipient who opted
out, and an abandoned-checkout reservation (also settled when the store
switched the reminder off, so turning it on later never sends a
months-old reminder).

Customer types are transactional with the `List-ID` the old send sites
used (`order_confirmation`, `order_status`, `order_invoice`, ...), set as
`list_id` so the recorded headers stay equal. The two carrier
arrival mails (`shipping.acs_out_for_delivery`, `shipping.boxnow_at_locker`)
sent no list headers before they moved; like every customer transactional
mail they now carry them (re-recorded in the goldens):

```
-  "headers": {},
+  "headers": {
+    "List-ID": "<acs_out_for_delivery.shop.example>",
+    "List-Unsubscribe": "<mailto:owner@store.test?subject=unsubscribe>"
+  },
```

Their Reply-To was `[contact or From]` and is now `[contact]`, or none for a
store without a contact address (replies then go to From). The status template that
`order_returned` and `order_refunded` build on is `emails/base/order_status`,
not an email.

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
6. Per recipient: **opt-out** (marketing only, `SKIPPED_OPTED_OUT`), then
   `render` in `recipient.language`, `build_headers`, and
   `transport.deliver`.

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

The skips are logged as a structured line (`email_type`, `status`,
`reason`). There is no log table yet.

### Language

The language is the recipient's. The resolvers state it: `customer_of`
(an `Order` or a `UserAccount`: its own `language_code`, else the account's,
else the store's), `store_operators` (the store's), `platform_operators`
(`settings.LANGUAGE_CODE`). `store_operators` is the bound tenant's
`contact_email` then `owner_email` and nothing else: a store with neither
has nobody to write to, and `settings.ADMINS` is never used for it. When
both exist and differ, each gets its own mail. `team.new_order` and the
dispute mails and the ops alerts changed to this from
`tenant_admin_recipients()` (contact address only, in practice, for the
dispute mails), which is deleted.

### Sender

`From` is `tenant.credentials.tenant_from_email()` read in the schema the
send runs under, so a platform type is sent as `"{SITE_NAME}"
<DEFAULT_FROM_EMAIL>` and a store type as the store (its own verified domain,
else its name on the platform's sender). `Reply-To` (`sender.reply_to(email_type)`) is the store's contact
address for a store type sent to customers, and empty for a platform type
and for mail to the store's own staff.

## Gates (`gates.py`)

Each gate has a `label` (the reason a store reads, "Gift cards feature")
and `is_open()`. All are frozen, keyword-only dataclasses.

| Gate | Open while |
|---|---|
| `FeatureGate(feature=...)` | `tenant.features.feature_on(feature)`; the name must be in `STORE_FEATURES` (checked when the gate is built). |
| `StoreSwitchGate(setting=...)` | a server-side store setting is on (`MYDATA_ENABLED`); the name must be declared in `EXTRA_SETTINGS_DEFAULTS`. |
| `CarrierGate(carrier=...)` | the `ShippingProvider` with that code is active. |
| `ScheduleGate(entry=...)` | the `django_celery_beat` `PeriodicTask` of that name is enabled (read in `public`). |

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

### The store's email page (`emails/admin.py`, `emails/switches.py`)

`Settings -> Emails` in the store admin. One dedicated page, like the text
editor, not a changelist: `EmailSwitchAdmin.changelist_view` lists every
`Owner.STORE` type grouped by `EmailType.area` (`switches.AREA_TITLES`, one
title per area; `tests/unit/emails/test_switches.py` fails when a type is
declared in an area with none), with its title and description from the
declaration. A required type shows a "Required" label and no switch; a type
whose gate is closed also shows "Off: <gate label>", whatever the switch
says. A switch posts to `toggle/` (htmx), which calls `set_switch` and
answers with the row; a required or unknown type is a 400 or 404 and writes
nothing. The page reads `EmailSwitch` directly, not the pipeline's cached
list, so it shows what was just written.

Permission: `emails` is in `STORE_SETTINGS_APP_LABELS` and
`STORE_SHARED_APP_LABELS` (`tenant/role_scopes.py`), so ADMIN and OWNER hold
`emails.view_emailswitch` and `change_emailswitch`; STAFF hold neither and
the page answers 403 and is not in their sidebar. The audit is the
simple-history row with the staff member (`EmailSwitch.history`). Platform
types (`Owner.PLATFORM`) are not on this page.

## Opt-out (`optout.py`)

Only marketing types honour an opt-out; a receipt or a security mail is not
something a person can decline.

- `EmailOptOut(address_hash, type_key, source)` is unique per
  `(address_hash, type_key)`. `type_key` is a marketing type's key, or `*`
  for every marketing type.
- `address_hash(address)` is HMAC-SHA256 of the stripped, lower-cased
  address under `settings.EMAIL_HASH_KEY`: the table holds no address.
- `make_token(address, type_key)` signs `{schema, h, t}` with
  `django.core.signing` under the salt `emails.optout`. It names the schema
  because `SECRET_KEY` is global: a token from one store does not verify on
  another. `read_token` / `opt_out_by_token` raise `InvalidToken` for a bad
  signature, an expired token (365 days), another store, an unknown type or
  a type that takes no opt-out.
- `unsubscribe_url` builds `<api host>/api/v1/emails/unsubscribe/<token>`
  (`UNSUBSCRIBE_PATH`); `EmailUnsubscribeView` serves it (below).

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
- **There is no `GET`** (it answers `405`). A link scanner follows every URL
  in a mail, so a GET must never opt anyone out. The plan's GET redirect to
  the storefront confirm page waits for that page; until it ships, no mail
  body links to this URL, only the header does.
- The blanket newsletter link (`UnsubscribeAllView`, `/user/unsubscribe/<token>`)
  also writes `EmailOptOut(type_key="*")` for the address the link was sent
  to, beside the account-level `marketing_opted_out_at` (state, not a gate).

### `EMAIL_HASH_KEY`

A required environment variable in production **and staging** (any
non-dev, non-CI deployment: the process refuses to start without it), so it
must exist in the environment BEFORE the release containing `emails/`
deploys. Development and CI get a fixed insecure key. Generate one
with `secrets.token_urlsafe(48)` and keep it with the other platform
secrets. It is separate from `SECRET_KEY` on purpose: rotating `SECRET_KEY`
must not orphan the opt-outs. Changing `EMAIL_HASH_KEY` forgets every
opt-out (the hashes no longer match, and outstanding unsubscribe links
stop working), so it is never rotated casually.

## Headers (`headers.py`)

`build_headers(email_type, recipient)` decides by audience, then by kind.
Only `customer` mail is list mail; `store_staff` and `platform_operator`
mail gets no list headers (`{}`), as staff mail has none today. For
customers:

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
pipeline's own (`/api/v1/emails/unsubscribe/<token>`), never the blanket
link of `user.utils.subscription`, which only the re-engagement body still
prints.

## Header safety

A subject is the only header built from data a store writes (a product or
company name). `render.clean_header_text` turns every whitespace run
(carriage return and line feed included) into one space, drops control
characters and caps the length at 200, so a name containing `\r\nBcc: x`
cannot add a header. `transport` relies on Django's own refusal as the
backstop: an address with a line break raises `BadHeaderError`. Data-derived
subject pieces reach the subject through the context builder, never by
concatenation at a call site.

## Not built yet

- `EmailLog`, provider events, suppression and a deferred (`defer`) send;
  skips are only a structured log line, and there is no `X-Entity-Ref-ID`.
- The storefront confirm page and the `GET` redirect to it, and the
  storefront's email-preferences UI (the endpoint exists).
- The other domains' email types (allauth, the platform's own mail) and
  the rest of the admin page (status, log, preview, test-send); the switches
  are on it already. The preview of every registered type already
  goes through `render`; a real object is chosen only for the order types
  (`order.emails.ORDER_EMAIL_KEYS`), every other type previews its `sample`.

## Tests

- `tests/unit/emails/`, `tests/integration/emails/`, `tests_mt/test_email_isolation.py`:
  the registry, gates, switch, opt-out, headers, header safety and the
  per-schema isolation. The contract is proven over probe types
  (`tests/utils/emails.py`): the `catalogue` fixture swaps the registry's
  contents for the test and restores them. Never register a probe type in
  production code. A test that runs a store email (an order task) needs a
  store bound: `tests.utils.staff.store_tenant` + `bind_store_tenant`.
- `tests/integration/emails/test_golden_render.py` records every order
  email as it is sent today (subject, recipients, From, Reply-To, headers,
  attachment names, HTML and text, in Greek and English) under
  `tests/integration/emails/golden/`. A send site that moves onto the
  pipeline must keep these equal; a deliberate change re-records them in the
  same commit (`GOLDEN_UPDATE=1 uv run pytest <test>`, helper
  `tests/utils/golden.py`). Everything a body prints is pinned; the only
  normalised value is the footer's copyright year. The Greek files need
  the compiled catalogue (`manage.py compilemessages --ignore=.venv`); the
  test fails with that instruction rather than recording English.
- `tests/integration/emails/test_golden_render_domains.py` does the same
  for the product, gift card, B2B, contact, account and cart emails. It
  pins what the rows cannot (the user and product ids, the signer's clock,
  `SECRET_KEY`, `EMAIL_HASH_KEY`) because the factories draw at random.
- `tests/integration/emails/test_golden_render_ops.py` records the ten
  operational alerts through the real code that sends them. They had no
  template and no translation before, so the files record what they are now.
- `tests/integration/emails/test_switch_admin.py`: the page's permissions
  (ADMIN and OWNER yes, STAFF 403 and no sidebar entry), toggling changes
  what `send_email` does, history, the cache generation, and a required or
  unknown type refused.
- `tests/integration/emails/test_ops_alerts.py`: each real sender honours
  its switch and keeps its bookkeeping, a store with nobody to write to skips
  the mail, and carrier code imports no `send_mail`.
  `test_import_ops_alert_switches.py` runs `emails/0003` over the settings a
  store is in.
- `tests/integration/emails/test_every_type.py` is parametrised over
  `registry.all_types()`, so a type added to any `<app>/emails.py` is covered
  without editing a test: its sample sends, a closed gate stops it with the
  gate's label, a store's switch stops a switchable one and refuses a
  required one, the headers are exactly those of its audience and kind, an
  opt-out stops marketing mail and only that, and the preview is the mail
  sent.
- `tests/integration/emails/test_domain_tasks.py`: what each migrated task
  does with a skip (alerts stay armed, claims are released or settled).
- Add a golden case (the helper takes any sent message) before moving
  another domain's send sites onto the pipeline. A test about what an email
  says rather than whether it is sent uses the `open_email_gates` fixture.
