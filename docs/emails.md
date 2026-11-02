# Emails

The email pipeline: one catalogue of email types, one function that sends
them, and the switch and opt-out a store or a recipient uses to stop one.
Source: `emails/` (`types.py`, `registry.py`, `gates.py`, `recipients.py`,
`sender.py`, `headers.py`, `optout.py`, `render.py`, `send.py`,
`transport.py`, `resend.py`, `log.py`, `status.py`, `tasks.py`, `preview.py`, `figures.py`, `testsend.py`, `models.py`,
`services.py`), `tests/utils/emails.py` and
`tests/utils/golden.py` (the test helpers). The code wins over this page.

**State.** The order, shipping, product, gift card, B2B, contact, account,
cart, operational alert and allauth (account, MFA and social sign-in)
emails and the platform's own mail (billing notices, the staff password
reset, the health and backup alerts) are on the pipeline (see Registered
types). Each domain moved onto it in turn, keeping the recorded golden files
equal (see Tests).

## The pieces

| Module | What it does |
|---|---|
| `types.py` | `EmailType`, the contract of one kind of email, and `Audience`, `Kind`, `Owner`, `RenderEnv`. |
| `declare.py` | `customer_email`, `marketing_email`, `staff_email`, `platform_email`, `operator_alert`: the shapes a domain declares its types with. |
| `registry.py` | `registry`: `register`, `freeze`, `get`, `all_types`, `types_for`. |
| `gates.py` | The four gates a type can list. |
| `recipients.py` | `Recipient(address, language)`, the resolvers `customer_of`, `store_operators`, `platform_operators`, and `store_operator_addresses`, the store's own inbox list. |
| `sender.py` | The `From` (`from_address`) and `Reply-To` (`reply_to`, `store_reply_to`). |
| `headers.py` | `build_headers`: the list headers by kind, and the two builders under it. |
| `optout.py` | The address hash, `is_opted_out`, `opt_out`, the signed unsubscribe token. |
| `views.py`, `urls.py` | `EmailUnsubscribeView`, the RFC 8058 one-click target. |
| `preview.py` | `build_preview`, the admin preview of any registered type, and its form. |
| `figures.py`, `testsend.py` | The per-type sent and failed counts of the admin pages and the dashboard; the admin's Send test. |
| `render.py` | `render`: subject and both bodies in one language; `clean_header_text`; `owner_schema`, the schema a type is read and sent under. |
| `transport.py` | The pipeline's only sender: builds the message and hands it to the transport `EMAIL_TRANSPORT` names (SMTP through Django, or Resend), returning a `Receipt` (provider and its message id). |
| `resend.py` | The Resend HTTP client: which team's key, one `POST`, the error text. |
| `send.py` | `send_email`: the whole envelope. |
| `status.py` | `Status`: what became of one recipient's send, shared by `Delivery` and `EmailLog`. |
| `log.py`, `tasks.py` | The `EmailLog` writes of `send_email`, and the retention sweep. |
| `models.py`, `services.py` | `EmailSwitch`, `EmailOptOut`, `EmailLog`, `EmailEvent`, `EmailSuppression`; `set_switch`, the only writer of switches. |

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
| `newsletter.already_subscribed` | the `UserSubscription` (a guest row too) | required (the answer to the visitor's own form submission); `FeatureGate("newsletter")` |
| `user.reengagement` | the `UserAccount` | **marketing**; `ScheduleGate("send-inactive-user-notifications")` |
| `cart.checkout_abandoned` | the `Cart` | **marketing**; `FeatureGate("cart")` |
| `account.*` (12), `mfa.*` (5), `social.*` (2) | an `AllauthMail` | required; see allauth mail |

### The newsletter form's answer

`POST /api/v1/user/subscription/newsletter` answers every visitor the same
`202` and does every address-dependent step in `subscribe_to_newsletter_task`,
so neither the body nor the cost tells a known address from a new one. What the
address's owner learns arrives by email: a new, expired-pending, unsubscribed or
bounced address gets `newsletter.confirmation`; an address that is already
ACTIVE gets `newsletter.already_subscribed`. An account row's notice carries the
link to `/account/subscriptions/`. A guest row's carries no link: the mail
says the address IS subscribed, so it offers no unsubscribe; the copy points
at the unsubscribe link every newsletter carries.

Both mails share the resend cooldown (`NEWSLETTER_RESEND_COOLDOWN`, ten
minutes). The cooldown of the notice lives in
`UserSubscription.already_subscribed_notice_sent_at`, claimed under the row's
lock before the mail is queued, so concurrent submissions send one; the
confirmation keeps using `confirmation_sent_at`, which dates a link an active
row no longer has. A PENDING row inside the cooldown sends nothing: the link
that went out is the answer.
A mail server that permanently refuses every recipient of either mail marks the
row BOUNCED (the same rule, `is_recipient_refused`), so the notice is not queued
again for a dead address; a new submission re-arms it with a fresh link.

The one synchronous exception is about the caller's own data. A signed-in
account that submits one of its own VERIFIED addresses while already confirmed
(`holds_active_newsletter_subscription`) gets `status: "already_subscribed"`
in the same `202` and nothing is queued. Every other answer, signed in or not,
carries `status: "pending_confirmation"`. An anonymous request never runs that
check.

### Operational alerts (`ops.*`)

Ten mails to the store's own staff, for work that needs a person. They were
`OpsAlertKind` and `send_ops_alert` (`shipping/alerts.py`); each is now an
email type declared with `staff_email(..., area="ops")`, so the store's
`EmailSwitch`, the gates and `store_operators()` apply. All are switchable,
store staff, with no list headers. A store with no contact or owner address
gets `SKIPPED_NO_RECIPIENT` (the old `ADMINS` fallback is gone with
`tenant_admin_recipients`, deleted); the work still shows on the dashboard counter or
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
| `ops.builder_schedule_failed` | a `ScheduleFailed` (a failed scheduled page publish) | `builder/emails.py` | |
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
release rolls out; `emails/0004_drop_ops_alert_settings` (`contract_of`
0003) then deleted them in every schema. The declarations and the
"Operational Alerts" settings group are gone.

A marketing type is switchable, honours `EmailOptOut` and carries the
RFC 8058 headers. `EmailOptOut` is the ONE gate for every marketing type:
no task filters on `UserAccount.marketing_opted_out_at` any more. That
field stays as account state (the admin and the storefront page's "stop all"
button set it; the type-scoped unsubscribe writes `EmailOptOut` alone), and `emails/0002_import_marketing_opt_outs` copied it into a `*` row
for every account that opted out before the link also wrote `EmailOptOut`
(#368). In the customer admin staff may RECORD an objection a customer made
by phone or email (setting the field writes `EmailOptOut("*")`,
`UserAdmin.save_model`); once set the field is read-only, because only the
customer can lift an opt-out, through their own preferences (below).

### The platform's own mail (`platform.*`)

Seven types with `owner=Owner.PLATFORM`, declared with `platform_email` or
`operator_alert`. They are sent from `public` whatever store is bound
(`render.owner_schema`, entered by `send_email` and by `render`, so the
admin preview shows the mail that is sent): the platform's name, logo,
contact address and `"{SITE_NAME}" <DEFAULT_FROM_EMAIL>` sender, never a
store's. They are listed on no store's Settings -> Emails page
(`switches.switch_groups` reads `Owner.STORE` only). All are `required`:
the platform has no switch page, and a billing notice, a password reset or
an alert to the operators must not be mutable. None carries list headers
(store staff and operators).

| Key | Declared in | About | Audience, language |
|---|---|---|---|
| `platform.billing_expiring`, `platform.billing_expired`, `platform.billing_suspended` | `tenant/emails.py` | a `BillingNotice` (the `Tenant` row and the grace days), sent by `tenant.billing._send_stage_email` | the owner (`Tenant.owner_email`), in the store's `default_locale` |
| `platform.billing_stage_copy` | `tenant/emails.py` | a `BillingStageCopy` (store, stage), the operators' trail of stages 2 and 3 | `settings.ADMINS` (`platform_operators()`), `settings.LANGUAGE_CODE` |
| `platform.staff_password_reset` | `tenant/emails.py` | a `StaffPasswordReset` (the link, built by `tenant.tasks.send_staff_password_reset_email`) | the staff member's address, in the asked store's language |
| `platform.health_alert` | `core/emails.py` | a `HealthAlert` (the failed components and their errors), from `core.tasks.monitor_system_health` | `settings.ADMINS`, `settings.LANGUAGE_CODE` |
| `platform.backup_failed` | `core/emails.py` | a `BackupFailed` (the error), from `core.tasks.scheduled_database_backup` | `settings.ADMINS`, `settings.LANGUAGE_CODE` |

The staff reset is built under `public`, which knows only the platform, so
the task reads the store's name in the store's schema first and carries it
in the object (`store_name`): the subject and heading name the store, the
rest of the brand is the platform's (owner decision D2: the platform
sender). Its link still points at the host that asked, in the store's
language.

Both operator alerts keep their old guarantees: a failed health alert send
is logged and does not mask the failure the task raises, and the backup
task still re-raises its own error after a failed alert. The billing
copy never fails the owner's notice it follows. `mail_admins` is no longer
called anywhere in the project: an operator alert goes to the same
`ADMINS`, now with a template pair, a translated subject and the platform's
sender (the subject starts `[{site}]`, as the ops alerts do; `SERVER_EMAIL`
and `EMAIL_SUBJECT_PREFIX`, which only `mail_admins` read, are gone).

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
The admin still only writes (`opt_out_of_marketing`).

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

Every outcome is also a row of `EmailLog` (see The email log); a skip is a
row with its status and `reason` and no recipient work, and is still a
structured log line (`email_type`, `status`, `reason`).

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

`From` is `emails.sender.from_address()` read in the schema the
send runs under, so a platform type is sent as `"{SITE_NAME}"
<DEFAULT_FROM_EMAIL>` and a store type as the store (its own verified domain,
else its name on the platform's sender). `Reply-To` (`sender.reply_to(email_type, obj)`) is the store's contact
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

### The store's email pages (`emails/admin.py`, `emails/switches.py`, `emails/figures.py`)

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
(`figures.type_figures`, grouped by type, `@dashboard_query("store:emails:v1",
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

**Send test** (`emails/testsend.py`, `test_send_view`) mails the email to the
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

Permissions (`tenant/role_scopes.py`): `emails` is in `STORE_SETTINGS_APP_LABELS`,
so ADMIN and OWNER hold every `emails.*` permission (switch, send a test, remove a
suppression); it is also in `STAFF_READ_ONLY_APP_LABELS`, so STAFF only **look**
(the page, the log and the suppression list, with the switch disabled and no
Send test). The audit of a switch is the simple-history row with the staff member
(`EmailSwitch.history`). Platform types (`Owner.PLATFORM`) are not on these pages.

**Dashboard.** "Emails failing" in the store dashboard's Needs attention card
counts the last 7 days of failed, bounced, complained and provider-suppressed
rows (`figures.failing_since`, in `attention_counts`), and opens the log under
the same filter.

### The preview (`emails/preview.py`)

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
- The old `/api/v1/user/unsubscribe/<token>[/<topic>]` routes, which opted out
  on GET, are gone with their token (`user.unsubscribe` salt).

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
view: a 421 from the mail server then failed the sign-up request (production,
2026-09-15), where in the task it is a retry.

The 19 types are `account.*` (twelve), `mfa.*` (five) and `social.*` (two),
declared in `user/emails.py` through `_allauth_email`, which also fills the
prefix map. All are customer mail, transactional and **required**: a store
cannot switch a sign-in code, a reset link or a security notice off. Their
templates are `core/templates/emails/{account,mfa,social}/<name>.{html,txt}`;
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

The subject is the type's msgid, as everywhere on the pipeline. allauth's old
`[<store name>]` subject prefix is gone (the `From` carries the store's
name); `ACCOUNT_EMAIL_SUBJECT_PREFIX`, `format_email_subject` and
`get_from_email` are deleted, and so is `user.tasks.send_rendered_email_task`
(nothing has queued it since `send_allauth_email_task` replaced it).

## Transports (`transport.py`, `resend.py`)

`settings.EMAIL_TRANSPORT` (env, `smtp` or `resend`, anything else stops
boot) names the one transport that carries the pipeline's mail.

- **`smtp`** (default; production until the owner switches it, staging
  always): `EmailMultiAlternatives.send()` through
  `core.mail.DemoRecipientSuppressingBackend` over the `EMAIL_HOST*` relay.
  SMTP returns no message id, so `EmailLog.provider_message_id` stays null:
  an environment fact, not a fallback.
- **`resend`**: `POST https://api.resend.com/emails` with `requests`, one
  request per recipient. The body is `from`, `to`, `subject`, `html`,
  `text`, `reply_to` and `headers` (the list headers, `List-Unsubscribe`
  included, only when non-empty) and `attachments` (`filename`, base64
  `content`, `content_type`). `Authorization: Bearer <key>` and
  `Idempotency-Key: <EmailLog.uuid>` (Resend keeps a key for 24 hours).
  The returned `id` is recorded as `provider_message_id`.
  Two hidden **tags**, never headers: `schema=<schema name>` and
  `log=<EmailLog.uuid>`; they come back in the webhook's `data.tags`, which
  is how an event finds its row. No address is in a tag, header or log line.

A demo store's own mailboxes are dropped on both transports
(`core.mail.without_demo_mailboxes`); a message left with no recipient posts
nothing.

### Which Resend team

One Resend team per store that has its own verified domain (owner decision
D6); the platform's team carries the platform's own mail and every store
without its own domain.

- The store sends as itself when `emails.sender.own_sender()` is set: its
  `from_email` is set **and** `from_email_verified` is ticked. Then the key
  is `Tenant.resend_api_key` and **only** that: a store with its own domain
  and no key raises `ResendNotConfigured` (an `ImproperlyConfigured`, not a
  class a task retries; the row is `FAILED`) instead of borrowing the
  platform's, whose team does not own the domain.
- Any other store, and every platform type (sent under `public`), uses
  `settings.RESEND_API_KEY`. Empty raises `ResendNotConfigured`.
- `Tenant.resend_api_key` and `Tenant.resend_webhook_secret` are
  Fernet-encrypted (see `secret-encryption.md`), editable by platform staff
  only (the "Email & Security" tab of the platform's tenant admin); a store
  role sees "set / not set". The webhook secret is read by the webhook
  receiver (see The Resend webhook).
- `RESEND_API_KEY` is required in production **and staging** when
  `EMAIL_TRANSPORT=resend`; `EMAIL_HOST_PASSWORD` is required only for
  `smtp`.

### Guarding the switch (`emails/setup.py`, `emails/checks.py`)

A store that sends from its own verified domain and has no key cannot send
over Resend at all, so selecting `resend` while one exists would fail all its
mail for good. One definition (`setup.lacks_resend_key`, `stores_lacking_resend_key`:
Resend selected, active and not suspended, `from_email` set and verified, no
`resend_api_key`, one query on `public`) feeds three guards:

1. **Deploy guard.** `emails.E001`, a `database` system check, is an error
   naming each store and its domain. `migration_preflight` (step 0 of the
   PreSync hook, and CI) runs the database checks first, so a bad switch
   fails the hook before any pod rolls and the previous release keeps sending
   over SMTP. The web process does not query at boot; settings import only
   checks `RESEND_API_KEY`.
2. **Validation at the source.** `Tenant.clean` refuses a save that leaves a
   verified own-domain store without its key while Resend is selected: both
   ticking From Email verified without a key and clearing the key of a
   verified store (error on `resend_api_key`).
3. **Dashboards.** The platform dashboard (`PlatformAlerts`) lists each such
   store ("Emails can't be sent from <domain> until its Resend key is
   saved") with a link to it; the store's own dashboard (`StoreAlerts`, for
   whoever may change store settings, OWNER and ADMIN) tells the merchant
   that its emails are not being sent and to contact support, since only the
   platform can fix it.

All three are inert while `EMAIL_TRANSPORT=smtp`.

### Failures

Every failure is a `requests` exception, so `core.retry` decides as for any
HTTP call. A timeout, a dropped connection, a 5xx and a 429 are retried by
the calling task (`MAIL_TRANSIENT_ERRORS`); any other 4xx is permanent
(`is_permanent_http_failure`). The exception text is `Resend answered 422
validation_error`: the status and Resend's error name, never its message
(which can quote the address) or the request. The key is never logged.

The idempotency key is the delivery's uuid (see Delivery identity across
retries), so a task retry after a lost response sends the same key and Resend
answers with the first message instead of sending a second one.

## The email log (`EmailLog`, `log.py`)

One row per recipient's delivery, in the schema the mail is sent under: a
store's rows in its schema, a platform type's in `public`. `send_email`
creates it before the transport runs (`QUEUED`), so its `uuid` can travel
with the message (the idempotency key and hidden tag of the Resend
transport), then sets `SENT` (`sent_at`, `provider`, `provider_message_id`;
SMTP returns no id, so it stays null there) or `FAILED` (`error`). A skip is
one row with its status and, for a closed gate, the gate's label in `reason`.

**Delivery identity across retries.** `EmailLog.uuid` is the delivery's identity (`log.delivery_id`). Inside a Celery
task it is derived from the task id, which `retry()` keeps, plus the type, the
recipient hash and the subject; outside a task it is a fresh uuid4. A retry of
a send that failed (or whose response was lost) therefore finds the row its
earlier attempt wrote, moves it back to `QUEUED` and sends under the same
uuid, so one row and, on Resend, one idempotency key cover the whole chain. A
recipient whose row is already `SENT` is returned as sent without a second
send: a task that mails two recipients and fails on the second does not mail
the first again. Two different mails of one type to one recipient in one task
differ in their subject, so they stay two deliveries.

What a row holds: type key, status, language, the subject that went out, a
keyed hash of the address (`optout.address_hash`) and a masked form
(`j***@e***.com`). **It holds no address and no body**: a body is
re-rendered from the type and its object, and `error` is the failure's class
and SMTP or HTTP code, never its message (a relay's text can quote the
recipient). `tests/integration/emails/test_email_log.py` scans every column.
**The log never stops or changes a send.** A write that fails (the insert
before the send, the update after it, a skip) is reported as `Email <type> is
not logged` and swallowed: raising would keep an alert about a failing
database from going out, and would make the task retry, and so send twice, a
mail that has already left. The send then simply has no row.

`EmailEvent` (the provider timeline of one row, unique on the provider's
event id) and `EmailSuppression` (an address hash with a reason and a scope,
kept until removed) are filled by the webhook (next section).

## The Resend webhook (`webhooks.py`, `events.py`, `suppression.py`)

`POST /resend/webhook/`, mounted on **every** host (root-shared, like the Viva
and BoxNow receivers), because each Resend team registers the host that owns
its keys:

- a store with its own team registers its own API host and the delivery is
  verified with that store's `Tenant.resend_webhook_secret`, resolved by the
  host it arrived on;
- the platform's team registers the platform host, verified with
  `settings.RESEND_WEBHOOK_SECRET`; its events are routed by their signed
  `schema` tag to the store (or `public`) that sent the mail.

There is no fallback between the two secrets, and a host with none answers
503 (Resend retries until it is set).

1. **Verification** (`webhooks.verify_signature`): the **Standard Webhooks**
   library's `Webhook(secret).verify` (the `standardwebhooks` package, the
   reference verifier Svix maintains and the `svix` client itself delegates to;
   it has no dependencies of its own). Resend signs with Svix, whose three
   headers are `svix-id`, `svix-timestamp` and `svix-signature`; the adapter
   renames them to the `webhook-` names the library reads. The library checks
   HMAC-SHA256 over `"{id}.{timestamp}.{raw body}"` under the secret (`whsec_` +
   base64), accepts any one of the `v1,` signatures in the header (several while
   a secret rotates), compares in constant time, and refuses a timestamp more than
   five minutes either way, which is the replay guard (fixed by the library, so
   there is no setting). Everything it can raise for a request that is not a
   delivery (a missing header, a malformed signature or secret, a body that is not
   UTF-8) becomes one `InvalidSignature`, so a forged request is a 400, never a
   500. The adapter is pinned by `tests/unit/emails/test_webhook_signature.py`
   against a signature made by the `svix` package itself. The body is verified
   before it is parsed, and capped at `RESEND_WEBHOOK_MAX_BODY_BYTES` (413). 400
   for anything else, with no payload in the log.
2. **Queue.** The view answers 200 at once and queues
   `emails.tasks.process_provider_event(svix_id, payload)`, which runs in the
   schema of the host that received it.
3. **Routing** (`events.route`): a store's endpoint speaks only for that
   store (a tag naming another schema is dropped), the platform's routes by
   the tag to `public` or an existing store, an unknown store is dropped with
   a warning.
4. **Matching** (`events.process`): the row must match BOTH the `log` tag
   (the row's uuid) and `data.email_id` (the provider's id), so a valid event
   can never update another row. A row not there yet (a fast event can beat
   the send's write of the id) retries up to four times with backoff, then
   ends as a recorded task failure.
5. **Dedupe:** `EmailEvent.provider_event_id` is the `svix-id`, unique per
   schema; a repeated delivery is recorded once and changes nothing.
6. **Effect:**

| Event | Row status | Also |
|---|---|---|
| `email.sent` | unchanged | recorded |
| `email.delivered` | `delivered` | |
| `email.delivery_delayed` | `delayed` | |
| `email.bounced`, type `Permanent` | `bounced` | `EmailSuppression` (hard bounce, scope all) |
| `email.bounced`, any other type | `delayed` | |
| `email.complained` | `complained` | `EmailSuppression` (complaint, scope marketing, D7) and an `EmailOptOut` for every marketing type |
| `email.failed` | `failed` | the reason token in `error` |
| `email.suppressed` | `provider_suppressed` | `EmailSuppression` (provider, scope all) |

   The status only moves forward (`events.RANK`: sent, delayed, delivered,
   failed, bounced or suppressed, complained), because events arrive out of
   order. `detail` keeps the bounce type and sub-type, the failure reason or
   the suppression type as fixed tokens, never an address or the provider's
   message. Open and click events are not subscribed (D9); one that arrives is
   recorded and changes nothing.

**Suppression at send time.** `send_email` asks `suppression.blocks` before it
renders: `all` blocks every type, `marketing` only a marketing one. A blocked
recipient is a `skipped_suppressed` row and `Delivery`. A required mail (a
receipt, a security code) to an `all`-suppressed address is not sent either,
and is logged at WARNING so it is never lost silently. A wider scope replaces
a narrower row (a bounce after a complaint), never the reverse. Removing a row
is the admin's (PR 3.4), and must also remove the address in Resend's own list.

**Retention.** `EMAIL_LOG_RETENTION_DAYS` (store setting, default 90, `0`
keeps everything, validator `emails.log.validate_retention_days_setting`).
`emails.tasks.purge_expired_email_logs` deletes through
`core.retention.delete_in_batches`, in public from beat and per store through
`tenant.tasks.fanout_purge_expired_email_logs` (`include_suspended=True`);
events cascade, suppressions stay. Staging anonymisation deletes all three
tables (`devtools/anonymise/rules.py`).

## Not built yet

- A deferred (`defer`) send; there is no `X-Entity-Ref-ID`.
- The order note that tells staff a receipt was not sent because the address
  is suppressed (it needs a hook on `EmailType` the order types implement),
  and moving a newsletter subscription to `BOUNCED` on a hard bounce (the
  event carries an address, the log only its hash).
- The platform admin's own page for the platform types (they have no
  switches, so it would be a listing and the log).
- "Edit the wording" on an email's page (Step 4). A real object is chosen only
  for the order types, every other type previews its `sample`.

## Tests

- `tests/unit/emails/`, `tests/integration/emails/`, `tests_mt/test_email_isolation.py`:
  the registry, gates, switch, opt-out, headers, header safety and the
  per-schema isolation. The contract is proven over probe types
  (`tests/utils/emails.py`): the `catalogue` fixture swaps the registry's
  contents for the test and restores them. Never register a probe type in
  production code. A test that runs a store email (an order task) needs a
  store bound: `tests.utils.staff.store_tenant` + `bind_store_tenant`.
- `tests/unit/emails/test_webhook_signature.py`, `tests/integration/emails/test_resend_webhook.py`
  and `tests_mt/test_email_webhook_routing.py`: verification (changed byte,
  stale timestamp, other secret, no fallback between the two secrets, 503,
  413), every event's effect, the forward-only status, dedupe, matching on
  both ids, suppression scopes, `send_email` honouring the list, and the
  cross-schema isolation with real schemas.
- `tests/integration/emails/test_resend_setup_guard.py`: who is lacking a key
  (one query), the system check and `migration_preflight` failing, the
  `Tenant.clean` refusal, both dashboards and their roles.
- `tests/integration/emails/test_resend_transport.py`: the request (key,
  idempotency key, tags, attachments, headers), which key a store uses, the
  retry verdict of each failure, demo suppression. HTTP is mocked at
  `requests.post`.
- `tests/integration/emails/test_email_log.py`: a row for every outcome, no
  raw address in any column, a failure never reaches the row, the retention
  sweep. `tests_mt/test_email_isolation.py` proves a platform type's row is in
  `public` and a store's in its own schema.
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
- `tests/unit/emails/test_only_transport_sends.py` reads every module's
  syntax tree and fails when a module outside `emails/transport.py` imports or
  calls `EmailMessage`, `EmailMultiAlternatives`, `send_mail`,
  `send_mass_mail`, `mail_admins` or `mail_managers`: a send site that skips
  the pipeline skips the switch, the opt-out and the headers.
- `tests/integration/emails/test_golden_render_platform.py` records the
  platform's mail. The billing notices and the staff reset were recorded on
  the old send paths and kept equal, but for the deliberate differences (a
  named `From`, the platform's brand on the staff reset); the operator mail
  had no template, so its files record what it is now. It pins the platform
  name, sender, contact address and static host so the files do not follow
  `.env`.
- `tests/integration/emails/test_golden_render_allauth.py` records the
  19 allauth mails through the adapter, and fails for a type with no
  case. `tests/integration/user/test_allauth_flows_use_the_pipeline.py`
  drives the real headless endpoints (sign-up, password reset, login
  code), and `test_allauth_email_store_identity.py` discovers the kinds
  from the installed allauth and fails for one that is not mapped.
- `tests/integration/emails/test_golden_render_ops.py` records the
  operational alerts through the real code that sends them. They had no
  template and no translation before, so the files record what they are now.
- `tests/integration/emails/test_email_pages.py`: the status page (tabs,
  search, state filter, figures against a hand-computed fixture, flat queries),
  one email's page, the Send test (only the signed-in account, throttle,
  marked as a test), the log (masked, exact-address search, filters, read-only),
  the suppression list and its removal, and the dashboard count.
- `tests/integration/emails/test_switch_admin.py`: the page's permissions
  (ADMIN and OWNER switch, STAFF only look), toggling changes
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
