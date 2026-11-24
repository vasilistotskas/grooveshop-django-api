# Emails: the pieces and the registered types

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
domain app: each app declares its types in `src/<app>/emails.py`, next to the
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
  (see Header safety in [`sending.md`](sending.md)).
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

Declared in `src/order/emails.py`, next to the builders that read the order:

| Key | About | Requirement / gates |
|---|---|---|
| `order.received`, `order.payment_confirmed` | the `Order` | required; `FeatureGate("cart")` |
| `order.shipped` | the `Order` | required |
| `order.delivered`, `order.completed` | the `Order` | switchable |
| `order.review_request` | the `Order`, `REVIEW_REQUEST_DELAY_DAYS` days after its delivery | **marketing**; `FeatureGate("product_reviews")` |
| `order.canceled`, `order.returned` | the `Order` | required |
| `order.status_refunded` | the `Order`, closed as REFUNDED | required |
| `order.refunded` | one refund `PaymentTransaction` | required |
| `order.receipt_issued`, `order.credit_issued` | the `Invoice` / `RetailCredit` (PDF attached) | required; `StoreSwitchGate("MYDATA_ENABLED")` |
| `team.new_order` | the `Order` | switchable, store staff |
| `team.dispute_opened`, `team.dispute_lost` | an `OrderDispute` (order, dispute id, provider) | switchable, store staff |

Declared by the carrier apps through `order.emails.shipment_email`
(`src/shipping_acs/emails.py`, `src/shipping_boxnow/emails.py`): order
emails about the order's shipment, previewed from an order through the
carrier's `shipment_for_order`, with their templates in `emails/order/`:

| Key | About | Requirement / gates |
|---|---|---|
| `shipping.acs_out_for_delivery` | the `AcsShipment` | switchable; `CarrierGate("acs")` |
| `shipping.boxnow_at_locker` | the `BoxNowShipment` | required; `CarrierGate("boxnow")` |

Declared in `src/product/emails.py` (templates in `emails/product_alerts/`; the
ops alert `ops.review_pending` is under Operational alerts below):

| Key | About | Requirement / gates |
|---|---|---|
| `product.restock_alert` | a `ProductAlert` (restock) | **marketing**; `FeatureGate("product_alerts")` |
| `product.price_drop_alert` | a `PriceDrop` (the alert and the price reached) | **marketing**; `FeatureGate("product_alerts")` |
| `product.alert_confirmation` | a guest's pending `ProductAlert` (its confirmation link) | customer, transactional (it answers the request just made); `FeatureGate("product_alerts")` |
| `team.low_stock` | a `LowStock` (the products at their threshold) | switchable, store staff; `FeatureGate("catalogue")` |

Declared in `src/giftcard/emails.py` (`FeatureGate("gift_cards")` on all):

| Key | About | Requirement |
|---|---|---|
| `giftcard.delivery` | the `GiftCard`, to its recipient | required |
| `giftcard.purchase_receipt` | the `GiftCardPurchase`, to the buyer | required |
| `giftcard.expiry_reminder` | the `GiftCard`, to its recipient | switchable |

Declared in `src/b2b/emails.py` (`FeatureGate("wholesale")` on all):

| Key | About | Requirement |
|---|---|---|
| `b2b.profile_approved`, `b2b.profile_rejected` | the `BusinessProfile`, to its applicant | required |
| `team.b2b_application` | the `BusinessProfile`, to the store's staff | switchable, store staff |

Declared in `src/contact/emails.py` (the submitter is `Reply-To`):

| Key | About | Requirement / gates |
|---|---|---|
| `team.contact_message` | the `Contact` | switchable, store staff |
| `team.feedback` | the `Feedback` | switchable, store staff; `FeatureGate("feedback")` |

Declared in `src/user/emails.py` and `src/cart/emails.py`:

| Key | About | Requirement / gates |
|---|---|---|
| `user.data_export_ready` | the `UserDataExport` | required (GDPR) |
| `newsletter.confirmation` | the `UserSubscription` (a guest row too) | required (double opt-in); `FeatureGate("newsletter")` |
| `newsletter.already_subscribed` | the `UserSubscription` (a guest row too) | required (the answer to the visitor's own form submission); `FeatureGate("newsletter")` |
| `user.reengagement` | the `UserAccount` | **marketing**; `ScheduleGate("send-inactive-user-notifications")` |
| `cart.checkout_abandoned` | the `Cart` | **marketing**; `FeatureGate("cart")` |
| `account.*` (12), `mfa.*` (5), `social.*` (2) | an `AllauthMail` | required; see allauth mail in [`sending.md`](sending.md) |

### The newsletter form's answer

`POST /api/v1/user/subscription/newsletter` answers every visitor the same
`202` and does every address-dependent step in `subscribe_to_newsletter_task`,
so neither the body nor the cost tells a known address from a new one. What the
address's owner learns arrives by email: a new, expired-pending, unsubscribed or
bounced address gets `newsletter.confirmation`; an address that is already
ACTIVE gets `newsletter.already_subscribed`. An account row's notice carries the
link to `/account/subscriptions/`, except on a store with customer accounts off,
where no account has that page. A guest row's carries no link: the mail
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

Fourteen mails to the store's own staff, for work that needs a person. They were
`OpsAlertKind` and `send_ops_alert` (`src/shipping/alerts.py`); each is now an
email type declared with `staff_email(..., area="ops")`, so the store's
`EmailSwitch`, the gates and `store_operators()` apply. All are switchable,
store staff, with no list headers. A store with no contact or owner address
gets `SKIPPED_NO_RECIPIENT` (the old `ADMINS` fallback is gone with
`tenant_admin_recipients`, deleted); the work still shows on the dashboard counter or
the order note, whatever the switch says.

| Key | About | Declared in | Gate |
|---|---|---|---|
| `ops.shipment_rejected` | a `ShipmentRejected` (order id, carrier, its error, the remedy) | `src/shipping/emails.py` | |
| `ops.webhook_failed` | a `WebhookFailed` (carrier, message id, error) | `src/shipping/emails.py` | |
| `ops.stale_shipments` | a `StaleShipments` (the `AcsShipment` rows, threshold, clock) | `src/shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.unremitted_cod` | an `UnremittedCod` (the rows of `_unremitted_cod_rows`) | `src/shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.unattributable_cod_payouts` | an `UnattributablePayouts` (counts, payment date) | `src/shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.acs_voucher_request_unknown` | a `VoucherRequestUnknown` (order id, how the request ended) | `src/shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.acs_unrecognised_vouchers` | an `UnrecognisedVouchers` (the voucher numbers ACS named for the first time, the pickup date) | `src/shipping_acs/emails.py` | `CarrierGate("acs")` |
| `ops.payment_after_cancel` | a `PaymentAfterCancel` | `src/order/emails.py` | |
| `ops.payment_without_order` | a `PaymentWithoutOrder` (the intent id and what Stripe collected) | `src/order/emails.py` | |
| `ops.payment_mismatch` | a `PaymentMismatch` | `src/order/emails.py` | |
| `ops.overcharged` | an `Overcharged` (order, the payment that tipped it, the excess) | `src/order/emails.py` | |
| `ops.refund_failed` | a `RefundFailed` | `src/order/emails.py` | |
| `ops.builder_schedule_failed` | a `ScheduleFailed` (a failed scheduled page publish) | `src/builder/emails.py` | |
| `ops.gift_card_reversal` | a `GiftCardReversal` (the purchase, and what was already spent, or none when only disputed) | `src/giftcard/emails.py` | none |
| `ops.review_pending` | the `ProductReview` | `src/product/emails.py` | `FeatureGate("product_reviews")` |

`ops.gift_card_reversal` is not behind the gift cards feature, unlike the
other gift card mail: a store that switched the feature off can still have a
sold card whose payment is reversed or disputed, and that alert must not
vanish. The two outcomes share one type, one switch and one template (the
headline is chosen in the builder, under the recipient's language).

A task treats a skip by what it protects. The stale digest claims its rows
(`stale_alert_sent`), sends, and releases the claim when nothing was sent
(switched off, nobody to write to, ACS off): the parcels are in the first
digest after the store turns it back on. The order-bound alerts
(`payment_after_cancel`, `payment_mismatch`, `overcharged`, `refund_failed`,
`shipment_rejected`, `acs_voucher_request_unknown`) write their
`OrderHistory` note first, whatever the switch. The two ACS voucher checks
are sent once, when the state begins (`voucher_request_unknown_at`) or the
voucher is first recorded (`AcsUnrecognisedVoucher`), and stay counted on
the dashboard until staff resolve them. Every sender is best-effort: a mail failure never fails the task or
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
| `platform.billing_expiring`, `platform.billing_expired`, `platform.billing_suspended` | `src/tenant/emails.py` | a `BillingNotice` (the `Tenant` row and the grace days), sent by `tenant.billing._send_stage_email` | the owner (`Tenant.owner_email`), in the store's `default_locale` |
| `platform.billing_stage_copy` | `src/tenant/emails.py` | a `BillingStageCopy` (store, stage), the operators' trail of stages 2 and 3 | `settings.ADMINS` (`platform_operators()`), `settings.LANGUAGE_CODE` |
| `platform.staff_password_reset` | `src/tenant/emails.py` | a `StaffPasswordReset` (the link, built by `tenant.tasks.send_staff_password_reset_email`) | the staff member's address, in the asked store's language |
| `platform.health_alert` | `src/tenant/emails.py` | a `HealthAlert` (the failed components and their errors), from `monitor_system_health` (`src/tenant/tasks.py`) | `settings.ADMINS`, `settings.LANGUAGE_CODE` |
| `platform.backup_failed` | `src/tenant/emails.py` | a `BackupFailed` (the error), from `scheduled_database_backup` (`src/tenant/tasks.py`) | `settings.ADMINS`, `settings.LANGUAGE_CODE` |

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

A guest's alert is a double opt-in (`src/product/alerts.py`). Every guest
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
