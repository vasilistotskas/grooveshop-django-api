# Order / Payment / Shipping / Notification System

**Reference for anyone (Claude included) maintaining this surface.**
Keep this file synchronised when invariants change. Cross-references
are file paths + line numbers; the system has enough load-bearing
"don't undo this" pieces that drift here is expensive.

Last refresh: 2026-10-12 (the payment ledger: partial refunds, §7.2 / §7.3; before that 2026-09-30: every payment start refuses a non-PENDING order, a change during the provider call wins with 409, webhook history/emails only for an applied event, BoxNow webhook 400 for a non-object, non-UTF-8 or too-deep body; before that: order acquisition source, admin payment-method column, then no mint before payment, payment-after-cancel ahead of the settled guard, admin status read-only, ACS pickup-list `blocked_unprinted`, postcode refusal). See git log for changes since.

## 1. Overview

Three orthogonal axes describe every order in the system:

| Axis | Values | Meaning |
|---|---|---|
| `Order.status` | `PENDING`, `PROCESSING`, `SHIPPED`, `DELIVERED`, `COMPLETED`, `CANCELED`, `RETURNED`, `REFUNDED` | The fulfilment state. |
| `Order.payment_status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `REFUNDED`, `PARTIALLY_REFUNDED`, `CANCELED` | The financial state. **Decoupled from `status`** — important for COD where the parcel can be SHIPPED while payment is still PENDING. |
| `Order.pay_way.settlement` | `online`, `courier_cash`, `carrier_terminal`, `offline_transfer` | How the money changes hands. Drives which create path runs (online → webhook completes payment; the rest → collected later) AND which carriers may serve the order — a carrier declares the settlements it can physically perform (`supported_settlements`). The `is_online_payment` / `requires_confirmation` booleans are DEPRECATED mirrors kept for one release; read `settlement`. |

Two order columns look like "the payment method" and are not
interchangeable:

- **`Order.pay_way_key`** — a snapshot of the `PayWayEnum` key the
  shopper chose, written by `Order.save()`. This is the customer-facing
  label; the storefront maps it through `payment_methods.*`. It is
  snapshotted because `pay_way` is `SET_NULL` and because renaming a
  key must not rewrite history.
- **`Order.payment_method`** — the GATEWAY that processed the charge
  (`stripe`, `viva_wallet`, `acs_cod`), written later by the
  payment-confirmation handlers and *matched on* by the Viva webhook
  and the ACS COD reconcile. Never render it to a customer.

Django-rendered surfaces (invoice PDF, merchant email, admin) use
`PayWay.display_name`, which resolves the key to a localised label. The
storefront gets the key instead and translates it itself: it owns the
payment-method labels, and a key stays stable across languages.

The admin order list's **Payment Method** column
(`OrderAdmin.payment_method_label`) is the one exception, because staff
want the PSP, not the pay-way name (Viva's seeded pay way is keyed
`CREDIT_CARD`): it shows `payment_provider_label(pay_way.provider_code)`
(`order/payment.py`, the provider class's `display_name`, never an
instance) and falls back to `get_pay_way_key_display()` for a method no
provider registers, such as cash on delivery. Both are in memory
(`pay_way` is `list_select_related`), so the column costs no query.

Each shipping provider (ACS, BoxNow, plus the built-in `flat_rate`
merchant delivery) is a `ShippingCarrierInterface` adapter
(`shipping/interfaces.py`) registered with `@register_provider` and
looked up via `get_provider(code)` by
`shipping/services.py::ShippingService`. The adapter pattern lets
new couriers plug in without touching the order code.

## 2. State machines

### 2.1 OrderStatus transitions (the `allowed_transitions` map in `OrderService.update_order_status`)

```
PENDING → PROCESSING → SHIPPED → DELIVERED → COMPLETED
   │           │           │          │
   │           │           │          └──► RETURNED → REFUNDED
   │           │           └──────────► RETURNED → REFUNDED
   │           └──► CANCELED
   └──► CANCELED
```

- `OrderService.update_order_status(order, new_status)` validates
  every transition against this table.
- Terminal states: `COMPLETED`, `CANCELED`, `REFUNDED`. No exit.
- Auto-transitions wired in code:
  - `PENDING → PROCESSING` on COD voucher mint (`AcsService._advance_pending_order_to_processing`, `BoxNowService._advance_pending_order_to_processing`).
  - `PROCESSING → SHIPPED` from carrier events (`_apply_order_status_transition` in both services).
  - `SHIPPED → DELIVERED` from carrier events.
  - `DELIVERED → COMPLETED` for paid orders (`OrderService.maybe_advance_to_completed`), applied inline by the carrier poll and the COD reconcile and swept hourly by `order.tasks.complete_paid_delivered_orders` for everything that lands outside those two moments (a payment entered by hand, a webhook after delivery, an inline attempt that failed once — prod orders 252, 253, 73, 92 on 2026-09-20).
  - `PROCESSING → SHIPPED` also from an ACS `attempted` state: a failed delivery attempt proves the parcel shipped (prod order 267 sat at PROCESSING for the ten days ACS held it). And a shipment the summary still calls `new` is lifted to `in_transit` when `ACS_TrackingDetails` holds any checkpoint that is not a print (`_has_left_sender`), because ACS reports `shipment_status=5` / `delivery_flag=0` / no reason for a parcel waiting at a Smartpoint (prod order 284).
  - A CANCELED shipment state (only ever produced by the admin retire action or a voucher cancel, never by tracking) routes the order through `OrderService.cancel_order` (`AcsService._cancel_order_for_dead_shipment`) — stock back, payment settled, cancellation recorded — never a bare status write. Reached from `manage.py reconcile_acs_order_status`.
  - Carrier bridge: when a carrier reports DELIVERED or RETURNED while the order is still PENDING/PROCESSING (state jump between polls / missed webhook), `_apply_order_status_transition` walks the missing SHIPPED step first. The SHIPPED hop is customer-silent on the RETURNED path (`update_order_status(..., silent_for_customer=True)`) — prod orders 179 & 189 (2026-07) were stuck because the direct PROCESSING → RETURNED was rejected forever.

### 2.2 PaymentStatus transitions

No explicit table — flips are direct assignments. Common paths:

| From → To | Trigger |
|---|---|
| `PENDING → COMPLETED` | `Order.mark_as_paid()` (called by Stripe + Viva success handlers, COD reconcile) |
| `PENDING → FAILED` | `OrderService.handle_payment_failed` (Stripe `payment_intent.payment_failed`) |
| `FAILED / PENDING / CANCELED → PENDING` | `POST /order/{id}/retry-payment` (`OrderViewSet.retry_payment`) mints a fresh Stripe PaymentIntent and reopens the payment — **only while `Order.status` is `PENDING`**. A CANCELED payment on a PENDING order (an intent canceled while the order waits) is retryable. Every endpoint that starts a payment (`create_payment_intent`, `create_checkout_session`, `retry-payment`, `confirm_agent_payment`) shares `OrderViewSet._payment_start_refusal`: a paid order gets 400 "This order has already been paid.", a CANCELED or otherwise advanced one 400 "This order is no longer open for payment." — before the provider is called, because `cancel_order` settles an unpaid payment to CANCELED too, and reopening it let a charge land on a dead order. The provider call itself runs unlocked; `PayWayService._record_opened_payment` then locks the row and, if its status, payment status or payment id changed meanwhile (a cancel, a webhook for the previous intent), raises `OrderChangedDuringPaymentError` — the view answers 409 and the new intent's client secret is never returned, so it can never be confirmed. |
| `COMPLETED / PARTIALLY_REFUNDED → PARTIALLY_REFUNDED / REFUNDED` | Every refund path writes a `PaymentTransaction` REFUND row and `PaymentLedger.record_refund` derives the status from the ledger's sums in that one place (§7.3): `OrderService.refund_order` (the in-app refund, the staff "Refund" action, a cancel), `handle_stripe_charge_refunded` (§4.2) and the Viva reversal webhook (`_handle_reversal_created`, event 1797, §4.3). PARTIALLY_REFUNDED while part of what was captured is still refundable, REFUNDED once none is. An order can go through PARTIALLY_REFUNDED any number of times. |
| `PENDING / PROCESSING / FAILED → CANCELED` | `Order.save()` on the `status → CANCELED` **or `RETURNED`** transition (`PAYMENT_CLOSING_STATUSES`, `Order.settle_unpaid_payment`, `order/models/order.py`) — one save shared by the service, the admin form and any script, never a second save from the cascade (that re-fires the transition). An order that closes unpaid owes nothing, so its financial state is final too: a canceled order (`0057_settle_canceled_unpaid_orders` backfilled 78 rows) and a refused or uncollected COD parcel that came back RETURNED (`0058_settle_returned_unpaid_orders` backfilled 34). A PAID return keeps COMPLETED until the refund moves it to REFUNDED. |
| `PENDING → COMPLETED` | `AcsService._mark_cod_order_paid_if_pending` (COD reconcile) |
| `CANCELED → COMPLETED` on a CANCELED order | `OrderService.record_payment_after_cancel` — a confirmed Stripe (`handle_payment_succeeded`, `checkout.session.completed`) or Viva (`_handle_payment_created`) charge for an order that is already canceled. Checked **before** the settled-state guard (`OrderService.is_payment_after_cancel`), because a canceled order always carries a settled payment and behind the guard the charge was dropped with a WARNING. Books the money (`mark_as_paid`), records `metadata["payment_after_cancel"]`, logs ERROR, emails ops, notes the order; the order stays CANCELED and nothing ships. Idempotent per payment id. A REFUNDED / PARTIALLY_REFUNDED canceled order still ignores a stale success. |

`Order.clean()` refuses a COMPLETED payment with `paid_amount` 0.00
unless deductions cover the whole total (`Order._payment_consistency_errors`)
— the admin form edits both fields. It is not a `CheckConstraint`: the
total is a sum over the order's lines.

## 3. Order creation paths

Two entry points in `order/services.py` (the legacy `OrderService.create_order`
— dead code with zero production callers — was removed 2026-08-24):

### 3.1 `create_order_from_cart` — payment-first / online
- **Caller**: Stripe Checkout flow.
- Sets `status=PENDING`, `payment_status=<provider-returned>`.
- **Does NOT** dispatch courier task here. Online payments defer
  the dispatch to the payment-success webhook (PR #1 commit
  `59527a87` — see `project_shipping_dispatch_on_commit.md`).

### 3.2 `create_order_from_cart_offline` — order-first / COD / Viva
- **Caller**: Nuxt checkout for offline pay-ways + Viva redirect flow.
- Sets `status=PENDING`, `payment_status=PENDING`.
- For **COD/offline**: dispatches courier task via
  `_dispatch_shipment_creation_task` immediately. The
  dispatch is wrapped in `transaction.on_commit` inside
  `ShippingService.dispatch_create_shipment_task` so the worker
  only sees the order after the create transaction commits.
- For **online (Viva)**: skips the immediate dispatch — Viva's
  webhook handler does it after payment confirms.

### 3.3 Delivery-address rules (`core/validators/address.py`)

Every address write is checked against the order's country row before
an order or saved address exists, so a courier never receives an
address it cannot place (prod order #316: street "1", street number
"70300", a non-numeric postcode, rejected by `ACS_Create_Voucher`).

- **Postcode**: normalised (trimmed, upper-case, whitespace collapsed to
  one space) and full-matched (ASCII) against
  `Country.postal_code_pattern`. The pattern and `postal_code_example`
  are Google's Address Data Service `zip` / first `zipex`, seeded by
  `country/migrations/0011_country_postal_code_format.py` and editable
  in the Country admin. A blank pattern means only "required".
- **Street** must contain a letter; **street number** must not itself
  match the country's postcode pattern (4+ characters).
- **Region**: required exactly when the country has any (`Country.
  has_regions`, an `Exists(Region…)` annotation `for_list()`/
  `for_detail()` add — never a prefetch, so the storefront's country
  picker can decide whether to show the region field per country
  without an extra query); when given, it must belong to the selected
  country. `country/migrations/0012_seed_iso_countries.py` seeds the
  full ISO 3166-1 list, most of which have no seeded regions at all —
  a region could no longer be unconditionally required at the
  serializer-field level (`UserAddressWriteSerializer.region` and
  `OrderCreateFromCartSerializer.region_id` are both optional; this
  validator is what actually enforces it, per-country).
- Applied in `OrderCreateFromCartSerializer.validate` (always; an
  unknown `country_id` is a field error), `OrderWriteSerializer` and
  `UserAddressWriteSerializer` (`address_update_errors`), and
  `Order.clean()` / `UserAddress.clean()` (`model_address_errors`, the
  admin). The last three judge the address only when the write changes
  `country`, `region`, `street`, `street_number` or `zipcode`, so an
  unrelated edit of an old record is not blocked by an address that
  predates the rules.
- Carrier code calls `normalize_postcode(value)` and
  `postcode_matches(country, value)`.
- The storefront applies the same rules inline from the same country row
  (`shared/utils/postalCode.ts`); only the normalisation and the two
  street rules are mirrored in code, and they must stay in lockstep.

### 3.4 Acquisition source (`order/attribution.py`)

Every order created by either checkout path gets exactly one
`OrderAttribution` row (`order/models/attribution.py`, one-to-one,
`related_name="attribution"`), written by `OrderAttributionService.record`
(`order/services.py`) right after `Order.objects.create` inside the
creation transaction. A checkout order with no signal at all gets a
`direct` row, so for checkout orders **no row** means placed before the
table existed (`0059_orderattribution`, no backfill), never "direct".
Orders made outside checkout — the demo seed (`devtools/demo_account.py`,
`bulk_create`) and the test factories — have no row either.

- **Input.** `OrderCreateFromCartSerializer.attribution`
  (`OrderAttributionInputSerializer`, write-only, optional): `utm_source`,
  `utm_medium`, `utm_campaign`, `click_ids` (parameter NAMES from
  `CLICK_ID_PARAMS`, never values), `referrer`, `landing_path`, and
  `agent_protocol` (`ucp`/`acp`). The agent gateway sets it, and
  `_attribution_input` believes it only when the request also carries the
  gateway's shared secret, which it sends as `X-Internal-Gateway` on
  every cart and order call (`is_internal_caller`, `tenant/internal.py`); from anyone else it is dropped and the order is
  classified from its other signals.
  `OrderViewSet._attribution_input` adds the request's `User-Agent`,
  which the Nuxt order proxy relays from the shopper
  (`clientIdentityHeaders`). Content is never refused, only shape: a
  checkout must not fail over a strange referrer.
- **Classification.** `classify()` is pure (no ORM, no request).
  First match wins: agent protocol → `utm_source` (`paid` when
  `utm_medium` matches GA4's paid rule or a paid click id is present,
  else `campaign`) → click id (`fbclid` is `social`, the rest `paid`;
  an Instagram in-app UA turns `fbclid` into `instagram`) → known
  referrer host (`KNOWN_SOURCES`) → in-app browser UA marker → any other
  external referrer (`referral`, the host as source) → `direct`. The
  store's own domains (`tenant_domain_set`) are never a referrer, so a
  return from a hosted payment page cannot become a source.
- **Stored.** `source_type` (`OrderSourceType`, `order/enum/attribution.py`),
  `source` (a registry key, the referrer host or the raw `utm_source`;
  empty for direct), `medium`, `campaign`, `referrer_host` (host only,
  no path or query), `landing_path` (path only). Every column is capped
  in `classify()`, not by the serializer.
- **Read.** `OrderSerializer.attribution` (`source_type`, `source`,
  `medium`, `campaign`; null without a row) on list and detail, loaded by
  `OrderQuerySet.with_attribution()`. API filters on `/order`:
  `source` (iexact on `attribution__source`) and `sourceType`
  (`order/filters.py`).
- **Admin.** `OrderAdmin.order_source` (badge coloured by
  `ORDER_SOURCE_TYPE_VARIANT`, `admin/displays.py`), the
  `attribution__source_type` and `OrderSourceFilter` filters, six
  read-only `attribution_*` fields in the "Additional Information" tab,
  and the dashboard's "Orders by source" doughnut
  (`admin/dashboard/store/queries.py: order_source`, drawn by
  `widgets.py: StoreOrderSourceChart`; last 30 days, top six plus
  "Other", orders without a row left out). `source_label()` turns a
  stored `(source, source_type)` into the name staff read.
- **Trust.** Every input but the agent protocol is shopper-controlled,
  so the row is analytics only and never an authorisation or pricing
  signal. The agent source type is the one verified claim (see Input).

Adding a known channel is one `KnownSource` entry (and, for a new
click-id parameter, one `CLICK_ID_PARAMS` entry, which changes the
generated `ClickIdsEnum` the storefront's capture is typed against —
regenerate the schema on both sides).

### 3.5 VAT on an order (`OrderItem.vat_rate`, `order/taxation.py`)

Prices on an order are VAT-inclusive. The VAT inside them is backed out
at **the rate the line was sold at**, which the order records and the
catalogue can no longer change.

- **The snapshot.** `OrderItem.vat_rate` is written when the line is
  created. Both checkout paths pass `cart_item.vat_percent` (the rate the
  cart backed the line's price out of, wholesale prices included), a
  promotion gift line passes its product's, and `OrderItem.save()`
  snapshots the product's current rate for every other writer (the order
  item admin and API, scripts); `bulk_create` skips `save()`, so
  `devtools/demo_orders.py` passes it itself. The column has
  `db_default=0` only so the release still serving during a rollout can
  keep inserting; a product with no `Vat` row is sold at 0. The item
  admin shows the rate read-only on add and editable afterwards, so
  staff can correct a wrong snapshot.
- **Nothing reads `product.vat` for an order again.** `order/invoicing.py`
  and `order/mydata/builder.py` get their lines from
  `order.taxation.order_vat_lines`, which reads `item.vat_rate`. Editing
  a `Vat` row, moving the product to another, or deleting the row (the
  FK is `SET_NULL`) changes neither the PDF nor the myDATA payload of an
  existing order. Regenerating a not-yet-registered invoice therefore
  keeps the original rates too. Lines that predate the column were
  backfilled (`0065`) from their product's VAT at deploy time, which is
  what the documents used before the snapshot existed.
- **Shipping and the payment-method fee.** Incidental costs the seller
  charges the customer belong to the taxable amount of the goods they
  accompany (VAT Directive 2006/112/EC art. 78(b); Greek VAT Code, Law
  5144/2024, art. 24(4)(a) — art. 19 of the repealed Law 2859/2000), so
  they take the goods' rate. A basket that mixes rates has no single
  rate for them: each charge is split over the rates in proportion to
  the goods' value at each (`_rate_weights`: undiscounted line gross,
  units when the goods are free; largest remainder, so the shares add
  up to the cent) and every share is taxed at its own rate. A
  single-rate basket puts 100% on that rate. There is no order-level
  "shipping VAT rate" to store: the split is a function of the
  snapshotted lines and the two amounts, and a stored copy could only
  disagree with the lines when staff edit them. A charge on an order
  with no goods (only reachable by staff deleting every line) raises,
  since no rate can follow nothing and a guessed one would be a wrong tax
  document. Every caller already surfaces it: `generate_order_invoice`
  logs and retries, the admin generate/regenerate actions show the
  message, and `submit_invoice` records a `BUILD` failure.
- **One computation, two documents.** Every line is split into a net
  rounded half-up to the cent and the VAT left of its gross, so each line
  adds up to what was charged, then summed
  (`order.taxation.split_gross`). `Invoice.subtotal`, `.total_vat`,
  `.total` and the PDF's VAT breakdown are sums of those lines, and the
  myDATA `invoiceDetails` / `invoiceSummary` are the same lines, so they
  agree to the cent for any order
  (`tests/unit/order/test_invoice_mydata_agreement.py`). The cart's
  `vat_after_discount` rounds the same way. `Invoice.shipping` and
  `.payment_fee` are the gross amounts, printed as reference rows;
  net + VAT is the whole document.

## 4. Payment paths

**Per-tenant payment identity.** Every tenant runs its OWN payment
accounts:

- **Stripe — separate accounts, not Connect.** dj-stripe lives in
  TENANT_APPS (per-schema tables); `StripePaymentProvider` resolves the
  key via `tenant.credentials.stripe_credentials()` and passes it per
  call (`api_key=`, never the `stripe.api_key` module global). A tenant
  with no `stripe_secret_key` simply has Stripe unavailable — there is
  no platform-account concept or fallback of any kind (the "platform"
  account was always just webside's own account). Webhooks are
  HOST-ROUTED per tenant:
  `https://api.<tenant-domain>/stripe/webhook/<uuid>/` — django-tenants
  selects the schema, then dj-stripe's UUID view verifies against the
  per-schema `WebhookEndpoint` row secret. Provisioning (APIKey row +
  Stripe endpoint incl. secret): `manage.py bootstrap_stripe
  --schema=<schema>` (idempotent; `--rotate-endpoint` to re-mint). Every
  tenant, including webside, backfills its own `stripe_secret_key` +
  `stripe_publishable_key` on the Tenant row before running
  `bootstrap_stripe`.
- **Viva Wallet** — all credentials incl. `source_code` and `live_mode`
  per-tenant (no settings fallback). The webhook route on the tenant's own
  API host resolves the tenant BEFORE the GET verification challenge,
  so per-tenant verification keys work with no view changes; POST
  delivery still resolves the order by payload as source of truth.
- **BoxNow** — HMAC secret per-tenant (`box_now_webhook_secret`),
  resolved from the signed `parcelId` before verification.
- **Availability gate** — `PayWayService.is_provider_configured()`
  hides credential-less providers from the shopper pay-way list AND
  refuses checkout-session/SPT creation for them.

### 4.1 Stripe online (`payment_intent.succeeded`)

Webhook handler: `order/signals/handlers.py::handle_stripe_payment_succeeded`.

```
payment_intent.succeeded → handle_stripe_payment_succeeded
                 │
                 ├── webhook_processed_{event_id} idempotency guard
                 │
                 ├── OrderService.handle_payment_succeeded(payment_intent_id, captured=amount_received)
                 │   ├── select_for_update on Order row
                 │   ├── PaymentLedger.record_charge (CHARGE row, whatever state the order is in)
                 │   ├── mark_as_paid → payment_status=COMPLETED
                 │   ├── _suppress_customer_status_notifications(PROCESSING) ← PR #7
                 │   ├── update_order_status(PROCESSING)
                 │   └── _dispatch_shipment_creation_task (on_commit)
                 │
                 │   └── returns PaymentEventOutcome(order, previous_payment_status, applied)
                 │
                 │   only when outcome.applied:
                 ├── OrderHistory PAYMENT row (previous = the real prior status)
                 ├── send_order_confirmation_email (on_commit)  ← order_payment_confirmed template
                 └── notify_payment_confirmed_live (on_commit, logged-in users only)
```

`applied` is False for a stale event against a settled payment, a
repeat of one already applied, and a charge on a canceled order
(`record_payment_after_cancel` books it and alerts ops): none of them
gets a history row or a customer email. `handle_payment_failed` returns
the same outcome, so a late failure never tells a customer who paid
that their payment failed.

The `_suppress_customer_status_notifications(PROCESSING)` call is
load-bearing: without it the customer receives both an
`order_payment_confirmed` email AND an `order_processing` email within ms
(PR #7).

**The charge is recorded on the payment ledger** (§7.3): `captured` is the
event's `amount_received` (what Stripe collected, not what was asked for),
the ledger key is the PaymentIntent id, and the row is written before any
guard, so a stale event, a repeat, a settled order or a charge on a CANCELED
order still leaves its CHARGE (for the last, that row is what lets staff
refund it in-app). `checkout.session.completed` records the same charge from
the session's `amount_total` under the session's PaymentIntent id, so the
`payment_intent.succeeded` that follows adds nothing. An event without
`amount_received` is dropped as malformed, like one without an id.

### 4.2 Stripe refund (`charge.refunded`)

Webhook handler: `handle_stripe_charge_refunded` (`order/signals/handlers.py`).

The event carries the charge's CUMULATIVE `amount_refunded` and no refund id (Stripe's own docs call fetching it an extra API call), so the handler brings the payment ledger up to that figure instead of counting events:

- It first makes sure the charge is on the ledger (`record_charge` keyed on the PaymentIntent id; the event carries the charge `amount`).
- What the ledger does not hold yet (`amount_refunded` minus the REFUND rows already attached to that charge) is recorded as ONE REFUND row keyed on the event id, and `PaymentLedger.record_refund` derives `payment_status` (§7.3).
- A refund this app made is already on the ledger under its own refund id, so the event that reports it adds nothing. The same holds for a redelivered event and for an older event arriving after a newer one (it reports less than the ledger holds). A dashboard refund next to an in-app one counts each once.
- Whatever the ledger records fires `order_refunded.send` with that row's `amount`, partial refunds included (§7.2).
- `Order.status` is NOT auto-mutated (REFUNDED requires RETURNED first per the canonical table; that's a business call).
- The `webhook_processed_<event id>` metadata flag is gone from this handler: the ledger is the guard. (The sibling payment handlers keep theirs.)

### 4.3 Viva Wallet online

Webhook handler: `order/views/viva_webhook.py::_handle_payment_created`.

Differences from the Stripe path that are easy to miss:

- **Row lock** is acquired in the view (`_process_event_in_tenant`), not inside
  a service method. The whole webhook body runs inside one
  ``select_for_update`` block.
- **Idempotency** uses the ``VivaWebhookEvent`` table
  (``order/models/viva_webhook_event.py``), unique on
  ``(transaction_id, event_type_id)`` and written last in the same
  transaction — not a metadata flag like Stripe's
  ``webhook_processed_{event_id}``.
- **Inline status mutation**: ``order.status = OrderStatus.PROCESSING``
  + ``order.save(update_fields=[...])``, NOT
  ``OrderService.update_order_status(...)``. The post-save signal
  still fires, but the canonical state-machine validator is
  skipped — Viva trusts that ``status==PENDING`` before the
  payment landed.
- **PR #7 PROCESSING suppression** is wired (mirrors Stripe): the
  handler calls ``OrderService._suppress_customer_status_notifications(
  order, "PROCESSING")`` immediately before the inline status flip
  so the customer doesn't get back-to-back payment-confirmed +
  order_processing emails when Viva confirms payment. Without
  this, the post-save handler would dispatch both.
- **No ``notify_payment_confirmed_live`` toast** — that's a Stripe-
  specific dispatch. Viva customers see the payment-confirmed email
  + the WS toasts that fire on later state changes (SHIPPED,
  DELIVERED).
- **The charge is recorded on the payment ledger** (§7.3) once the
  transaction is verified (it belongs to the order, the currency and
  amount match, status `F`): the verified amount, keyed on the
  transaction id, before the canceled-order and settled guards. A second
  payment under another of the order's checkout codes is a second CHARGE
  row (`Order.payment_id` still names only the latest).

**Refund webhook (1797, `_handle_reversal_created`).** The event's
`TransactionId` is the REFUND, a transaction of its own; `ParentId` is the
payment it reverses
([Viva](https://developer.viva.com/webhooks-for-payments/transaction-reversal-created/)).
Per Viva's [status and type tables](https://developer.viva.com/integration-reference/response-codes/)
that transaction reads `statusId` `F` (`R`, "fully or partially refunded",
is the status of the ORIGINAL payment), a refund or void
`transactionTypeId` (`VIVA_REFUND_TRANSACTION_TYPE_IDS`; the "Refund
Claimed" types are chargebacks, not refunds) and a **negative** `amount`.
The handler used to wait for the refund transaction itself to read
REFUNDED, which Viva never reports, so every real 1797 was skipped — and
recorded as processed, so Viva never retried. Nothing is read from the
unauthenticated body except `ParentId`:

1. `_verify_viva_refund` retrieves the refund transaction (an error raises
   → 500 → Viva retries, and the multi-tenant candidate loop moves on) and
   requires one of the order's issued order codes on it, `F`, a refund or
   void type and a negative amount; the refunded amount is `-amount`. The
   gift-card purchase path (`_process_gift_card_purchase_event`) goes
   through the same verifier with the purchase's own order code. The card
   is the whole purchase, so each verified refund is added to
   `GiftCardPurchase.refunded_amount` and the card is voided once the
   refunds reach the purchase amount (a refund id counts once: the
   `VivaWebhookEvent` row is its idempotency key); a part refund leaves the
   value the buyer still owns alone.
2. The refund is attached to the payment `ParentId` names, and that payment
   must be one of the order's own CHARGE rows on the ledger (§7.3): an order
   paid twice under two of its checkout codes holds two, and each refund goes
   against the one it reverses. Retrieve Transaction documents no parent id,
   so `ParentId` can only refuse, never grant: the payment it points at comes
   from our own rows. For an order whose capture was never recorded, a
   `ParentId` equal to the order's own `payment_id` is read from Viva once and
   recorded (unreadable → 500, Viva retries); anything else is skipped.
3. `PaymentLedger.record_refund` keys the REFUND row on the refund
   transaction id. A refund already there is skipped: Viva notifies every
   refund, including the one `refund_order` has just made, and redelivers
   events. This is the unique constraint doing the work, behind
   `VivaWebhookEvent`'s own guard.
4. The row's amount is `-amount` of the verified refund; `payment_status`
   is derived from the ledger's sums by `record_refund` (§7.3). Nothing is
   read from Viva about what was captured: the ledger holds it. There is no
   "is the order paid" status guard any more: a verified refund of a recorded
   payment is a fact whatever the status says, and an unpaid order has no
   CHARGE for it to name.
5. `order_refunded` fires with `amount` = this refund (§7.2);
   `Order.status` is not touched.

The dispatcher stores the handler's real outcome in
`VivaWebhookEvent.outcome`: every skip above (and a 1798 that changed
nothing) is `skipped`, a booked refund `processed`. Ops: each tenant's Viva
account needs the *Transaction Reversal Created* webhook subscribed
(Settings → API Access → Webhooks). Production had received no 1797 by
2026-10-10, so check the subscription before assuming that a refund made in
Viva's dashboard reached the order.

**Post-payment redirect (fixed 2026-07-02).** Smart Checkout's
success/failure URLs are static strings configured on the source in
the Viva merchant portal — the API accepts no per-order success URL.
Viva appends ``?t=<transaction_id>&s=<order_code>&lang=..&eventId=<int32
event code>&eci=..`` (developer.viva.com → Smart Checkout
integration). The portal success URL points at the storefront's
Nitro route ``/checkout/viva-return``, which calls the public
``GET /api/v1/order/viva_return`` and 302s to
``/checkout/success/{uuid}``. The endpoint resolves ``t`` →
``payment_id`` (post-webhook) with fallback ``s`` →
``metadata.viva_order_codes`` (every code the order issued, appended at
session creation, so it wins the browser-vs-webhook race). Pitfalls encoded in history:
``s`` is the 16-digit order code, NOT an ``F`` status flag, and
``eventId`` is an int32 Viva event code, NOT ``merchantTrns`` — two
earlier frontend implementations assumed otherwise and broke the
success redirect (customer landed on the homepage via the
``/checkout`` empty-cart bounce).

### 4.4 COD / PAY_ON_DELIVERY (offline)

```
1. create_order_from_cart_offline → status=PENDING, payment_status=PENDING
2. _dispatch_shipment_creation_task (on_commit) → courier voucher mints
3. AcsService._advance_pending_order_to_processing → status=PROCESSING
4. Carrier polls: shipment_state advances → status flips PROCESSING → SHIPPED → DELIVERED
5. Daily ACS COD reconcile (02:30 Athens, queries yesterday's payouts):
   AcsService._mark_cod_order_paid_if_pending → payment_status=PENDING → COMPLETED
   Then maybe_advance_to_completed → status=DELIVERED → COMPLETED
   (and the hourly complete_paid_delivered_orders sweep catches any
   DELIVERED + COMPLETED row that step missed).
   Always silent for customers (email + toast suppressed; the shopper
   paid in person and already got the DELIVERED notification) — both
   the beat task and the backfill command pass silent_for_customer.
   The collection goes on the payment ledger as a COD_COLLECTION row
   (provider `acs_cod`, reference the voucher, amount the voucher's
   `cod_amount`; BoxNow: `boxnow_cod`, the parcel id,
   `amount_to_be_collected`). Cash is not refundable through a provider, so
   it never counts toward what `refund_order` can refund (§7.3).
6. Customer email + WS toast at every meaningful transition.
```

## 5. Shipping integrations

Each carrier implements `ShippingCarrierInterface` in
`shipping/interfaces.py` and registers with `@register_provider`
(imported in its `AppConfig.ready()`). Two courier carriers today, plus
the API-less `flat_rate` adapter (`shipping/carriers/flat_rate.py`).

Rules both carriers share:

- **No shipment before payment.** Every order gets its carrier row in
  `pending_creation` at checkout, but the mint choke points
  (`AcsService.create_voucher_for_order`,
  `BoxNowService.create_shipment_for_order`) raise
  `shipping.exceptions.ShipmentAwaitingPaymentError` while
  `Order.awaits_online_payment` holds — checked on the order row loaded
  under the shipment lock. An online-paid voucher carries no COD amount,
  so a voucher minted for an unpaid card order would hand the goods over
  for free (prod order 304 was one admin click away, 2026-09-24). The
  tasks return `{"status": "awaiting_payment"}` with no retry and no
  alert; the admin buttons ("Issue ACS voucher now", "Create BoxNow
  parcel now") refuse with a message, because the task's refusal never
  reaches the page.
- **A permanent rejection is noted on the order.**
  `shipping.alerts.alert_admins_shipment_creation_failed` emails ops AND
  writes an `OrderHistory` note, both carrying the carrier's own remedy
  ("Fix the address on the order, then press Issue ACS voucher now").
  Nothing retries a business error, so re-dispatching is that button.

### 5.0 Shipping pricing: `ShippingRate`

`shipping.ShippingRate` (tenant schema, cross-schema FK to
`country.Country` — the same shape `Order.country` uses) is the single
source of a store's shipping price, availability and weight cap, one
row per `(provider, country, kind)`. It replaced a global
`CHECKOUT_SHIPPING_PRICE` / `FREE_SHIPPING_THRESHOLD` /
`BOXNOW_*` / `ACS_*` Setting sextet that priced every destination the
same and had nothing to say about whether a store shipped there at
all — the gap that blocked offering BoxNow-to-Cyprus without also
accepting Cyprus orders at the Greek flat rate.

- **`ShippingService.active_rate(provider_code, kind, country_code)`**
  is the single gate: an active provider, a registered adapter, the
  provider supporting `kind`, `is_kind_enabled`, and an active rate row
  for that country — or `ShippingUnavailableError`.
  `assert_available(...)` adds the weight-cap check
  (`ShippingWeightExceededError`); both are used directly by a
  free-shipping promo, which must not let an unavailable or over-weight
  option through just because it charges nothing.
- **`ShippingService.quote(...)`** is `assert_available` + pricing:
  the rate's own `free_shipping_threshold` first, then the carrier's
  `live_quote()` hook (ACS's weight-banded `ACS_Price_Calculation`,
  gated on `ACS_DYNAMIC_PRICING_ENABLED`; every other carrier returns
  `None` and the rate's stored `price` wins), and is
  `OrderService.shipping_cost`'s only pricing path — no generic
  fallback survives it.
- **`ShippingCarrierInterface.live_quote(rate, country_code, region_id,
  weight_grams, currency)`** replaces the old
  `calculate_shipping_cost`/`free_shipping_threshold` pair. A
  transient failure returns `None`, never raises — the stored rate
  price is the safety net.
- **`/shipping/options`** requires `country_code` (400 without) and
  resolves the matrix from active rates for that country; each row
  carries `country_code`, `max_weight_grams` and `exceeds_max_weight`
  — an over-cap option (a heavy Cyprus BoxNow cart) is still listed,
  flagged, so the storefront can disable it with a reason instead of
  the step silently having one fewer card.
- **Delivery estimate**: `delivery_days_min`/`delivery_days_max`
  (business days after dispatch, both null = no estimate advertised,
  nothing invented) are on the rate and on each `/shipping/options`
  row as day counts only, never dates, so the response stays cacheable.
  `Order.estimated_delivery` (a date, the max bound) is fixed at
  creation by `OrderService._seed_estimated_delivery` and read from
  `tracking_details.estimated_delivery`; later rate edits do not move
  it. `shipping/delivery.py` owns the arithmetic: dispatch is the
  placement day when the order lands before the public
  `DISPATCH_CUTOFF` setting (`HH:MM`, store local time, an order at
  exactly the cutoff misses it; empty = no cutoff), otherwise the next
  business day; weekends and Greek public holidays (fixed dates plus
  Orthodox-Easter-relative ones) are not business days. The Greek
  calendar governs the dispatch day because every parcel leaves from
  Greece, whatever the destination country.
- **`/shipping/free-shipping-info`** defaults to the first
  `ShippingService.shippable_country_codes()` entry when no
  `country_code` is given, echoing whichever it used.
- **`?shippable=true` on `/country`** filters to
  `shippable_country_codes()`; always empty on the platform host,
  which has no tenant schema of its own to hold a rate.
- **`ShippingService.resolve_home_delivery_provider(country_code)`**
  picks the lowest-priority active home-delivery provider that has a
  rate for the country (or, with no country yet, the lowest-priority
  active one regardless) — shared by `_resolve_shipping_provider` (the
  order's FK) and `shipping_cost` (the price), so both always agree on
  which carrier a plain `home_delivery` request routes to.
- **Release N migration** (`shipping/0011_convert_legacy_pricing_to_
  rates.py`) copies each tenant's existing Setting values — or the
  frozen historical default when a row is missing — into GR-only rate
  rows (`metadata['supported_countries']` when set, else `["GR"]`), so
  no store starts offering a new country just because this shipped.
  The six Setting keys themselves are dropped in release N+1
  (`0014_drop_legacy_shipping_settings.py`, additive-only rule —
  `docs/migrations.md`).
- **Adding a country to an existing carrier** is an admin action, not a
  deploy: add a `ShippingRate` row (`ShippingProviderAdmin`'s Rates
  inline) for `(provider, country, kind)`.

### 5.1 ACS Courier (`shipping_acs/`)

- **REST API**, polling-based (no webhooks).
- Voucher mint: 3-phase `claim → API → persist` design in
  `AcsService.create_voucher_for_order`. Survives
  `idle_in_transaction_session_timeout` — see
  `project_acs_voucher_orphan_prevention.md`. **TTL 300s** (PR #6).
  `_build_create_voucher_params` uses `order.country_id` verbatim as
  `Recipient_Country` (no lookup — `Country.pk` IS the alpha-2 code)
  and refuses with `AcsAPIError` if it is missing, rather than
  defaulting to "GR" — ACS is Greece-only today, but which countries
  it serves comes from active `ShippingRate` rows (§5.0), never a
  hardcoded fallback here.
- Polling: `poll_shipment_tracking` runs in two phases (read,
  no lock; API call, no transaction; persist with
  `select_for_update`) — survives slow ACS responses without
  losing `last_polled_at`.
- COD: `reconcile_cod_payouts` runs daily, upserts `AcsCodPayout`
  rows from the ACS COD beneficiary endpoint, and flips
  `Order.payment_status` for matched vouchers. **Wire schema**: the
  voucher number arrives in the `POD` column (there is no
  `Voucher_No` in this response); `Customer_RefNo_1/2` echo
  `Reference_Key1/2` from voucher mint (`order.id` / `order.uuid`)
  and are the fallback match keys. Unmatched rows are counted as
  `skipped` and alert ADMINS. Backfill missed dates with
  `manage.py reconcile_acs_cod --days N [--silent]` (`--silent`
  suppresses customer COMPLETED notifications — mandatory for
  backfills).
- **COD received outside ACS**: the nightly `alert_unremitted_cod_payouts`
  treats a delivered COD parcel as unremitted unless an `AcsCodPayout`
  exists for its voucher (ACS's own statement). When the merchant was
  paid by another route and ACS never reports it (prod order 73), an
  operator uses the **Record COD received outside ACS** detail action on
  the `AcsShipment` admin (a note is required). It calls
  `AcsService.record_cod_received_outside_acs`, which refuses unless the
  shipment is a DELIVERED COD with an amount due, has no `AcsCodPayout`
  and is not already recorded; it stamps `cod_received_outside_acs_at/_by/
  _note` (audited by simple-history) under the shipment row lock and marks
  the order paid through `_mark_cod_order_paid_if_pending` (silent, a
  no-op if already paid). `_unremitted_cod_rows` then skips the parcel. No
  `AcsCodPayout` is ever created for it.
- HTTP 403/406 (`AcsAuthError`) is **retryable**: prod ACS returns
  sporadic transient 406s (~2% of tracking polls, self-healing —
  verified 2026-07-11); only a persistent rejection means a bad
  key/IP.
- A refused or uncollected COD parcel: ACS records `attempted` (order →
  SHIPPED), texts the customer and holds the parcel about ten days, then
  reports `returned_flag=1` → shipment RETURNED → order RETURNED, and the
  save settles the uncollected payment to CANCELED. Stock is not touched
  by that transition — the goods arrive back days later and may be
  damaged, so restocking is a staff decision on the physical parcel.
- Staleness watch: `check_stale_acs_shipments` (daily 09:00 Athens)
  emails ADMINS about non-terminal shipments with no tracking event
  for `ACS_STALE_SHIPMENT_DAYS` (default 3) days. Dedup via the
  `stale_alert_sent` claim flag, re-armed by the poller when a new
  event arrives. Dead vouchers are retired by a human via the admin
  action "Retire selected shipments" (local CANCELED — stops the
  poller; does not call `ACS_Delete_Voucher`).
- Manifest pre-flight: ACS refuses the whole pickup list while ANY
  voucher on it is unprinted, so "print everything outstanding" is the
  operation that unblocks it. The shipment changelist action "Print
  labels for selected shipments" returns ONE merged PDF (ACS has no
  bulk print alias — `ACS_Print_Voucher` takes a single `Voucher_No`,
  so `AcsService.fetch_labels_pdf` concatenates them); pair it with the
  `Label printed at` / empty filter. A voucher that fails is reported
  and the rest still print. Downloading stamps `label_printed_at`
  through the same `fetch_label_bytes` choke point as the per-order
  button.
- An "empty day" is not a refusal. `ACS_Issue_Pickup_List` takes a date
  and no voucher list — ACS picks the vouchers, scoped by
  `Pickup_Date` — so when every local candidate is a dead voucher (no
  tracking event, older than `ACS_STALE_SHIPMENT_DAYS`, and not the
  product of a re-mint) the service logs them at WARNING and returns
  `None` instead of raising. One live candidate still makes a refusal a
  failure. Those dead rows are reported by `check_stale_acs_shipments`,
  which owns them; this path deliberately does not send a second alert.
- An unprinted-labels refusal (`Unprinted_Found > 0`) is a status, not
  a failed task. The service raises `AcsUnprintedVouchersError`; the
  daily task returns `{"status": "blocked_unprinted", "unprinted": [...],
  "order_ids": [...]}` and notes each blocked order. Nobody is emailed,
  and there is no 15:45 warning any more: on webside the labels are
  printed the next morning, so the 16:30 run is refused most days, and
  ACS scans a printed parcel as collected the same day with or without a
  manifest (2026-09-21 to 10-10: 43 of 45 parcels collected with no
  pickup list, Saturdays included, when no list runs at all). The mails
  were daily noise the merchant asked to stop. To issue a list anyway:
  "Print labels for selected shipments", then "Issue ACS pickup list
  now". Every other refusal is a plain `AcsAPIError` and still fails the
  task. The retired `warn-unprinted-acs-vouchers` beat row is deleted by
  `tenant/migrations/0049`, because `DatabaseScheduler` never removes a
  row whose entry left `CELERY_BEAT_SCHEDULE`.
- The stale digest's "stranded mint" class (`pending_creation` > 24 h)
  excludes orders awaiting their online payment — the auto-cancel owns
  those, and listing them invited an "Issue ACS voucher now" for unpaid
  goods.
- `Recipient_Zipcode` is refused locally when the order's postcode does
  not match its country (`core.validators.address.postcode_matches`):
  a non-retryable `AcsAPIError` naming the field, recorded in
  `metadata["last_error"]` with the mint claim released. A valid one goes
  out with its space removed. The old digits-only filter turned a
  4-letter postcode into `""` and ACS answered with its generic "fill
  data error" (order 316, 2026-09-24).

### 5.2 BoxNow (`shipping_boxnow/`)

- **REST + webhook** for tracking events.
- Voucher mint: same 3-phase design as ACS.
- "Create BoxNow parcel now" (shipment detail action) re-dispatches a
  row left in `pending_creation` by a business error, once its data is
  fixed — the counterpart of ACS's "Issue ACS voucher now".
- Webhook handler: `BoxNowService.apply_webhook_event` — idempotent on `webhook_message_id`.
- The webhook view (`shipping_boxnow/views/webhook.py`) answers 400 (BoxNow does not retry) to any body it cannot read as a JSON object: bytes that are not UTF-8 (decoded strictly first, RFC 8259 §8.1 — `json.loads` on bytes would sniff UTF-16/32, while the signed-data extractor walks the raw bytes), invalid JSON, JSON nested past the decoder's stack, or valid JSON that is not an object (`[]`, `"x"`, `null`). Those used to raise inside the unauthenticated view and return 500, which BoxNow retries.

### 5.3 Adding a new carrier

1. Create `shipping_<provider>/` Django app.
2. Implement `ShippingCarrierInterface` (`shipping/interfaces.py`):
   - The abstract methods: `create_shipment`, `cancel_shipment`, `fetch_label_bytes`,
     `fetch_tracking_events` (return `[]` when events arrive by webhook), `shipment_for_order`,
     `serialize_shipment`, `validate_order_payload`.
   - `dispatch_create_shipment_task(self, order, *, schema_name: str | None = None)` — enqueues the
     Celery task that mints the voucher. `ShippingService` always passes `schema_name` (captured
     before `on_commit`); accept it and stamp it on the task (`headers={"_schema_name": ...}`), or the
     worker runs under the wrong tenant. The default is a no-op for carriers with no async step.
   - Webhook-based carriers handle events in their own service (BoxNow:
     `BoxNowService.apply_webhook_event`); it is not part of the interface.
3. Decorate the adapter with `@register_provider` and import it in the app's `AppConfig.ready()`.
4. Add `ShippingProvider` row in DB (admin or migration).
5. Implement `_apply_order_status_transition` if your carrier emits state events.
6. Wire the COD path if applicable (see ACS for the pattern).

## 6. Email + WS notification graph

### 6.1 Email triggers (final state, post PR #8)

| Event | Email task | Trigger | Idempotency flag |
|---|---|---|---|
| Order created (offline) | `send_order_confirmation_email` | `order_created` signal | `confirmation_email_sent` |
| Order created (online, pending payment) | — | (deferred to payment success) | n/a |
| Payment succeeded (online) | `send_order_confirmation_email` | webhook handler | `confirmation_email_sent` |
| Payment failed | `send_payment_failed_email` | webhook handler | `payment_failed_email_sent` |
| Status changed → DELIVERED / CANCELED / COMPLETED / REFUNDED / RETURNED | `send_order_status_update_email` | `order_status_changed` signal | `status_update_email_sent_<status>` |
| Status changed → PENDING / PROCESSING | — | (internal milestones — never a customer email) | n/a |
| Order genuinely SHIPPED (status SHIPPED **and** tracking present) | `send_shipping_notification_email` | `order_status_changed`→SHIPPED **or** `order_shipment_dispatched`, whichever completes both conditions | `shipping_notification_email_sent` |
| Refund (in-app, staff action or webhook): one per refund | `send_refund_confirmation_email` | `order_refunded` signal (PR #8) | `refunds_emailed` (how many ledger refunds the emails sent cover, §7.2) |
| Invoice generated | `send_invoice_email` | from `generate_order_invoice` | `invoice_email_sent` |
| Dispute opened (staff) | `send_dispute_notification_email` | `charge.dispute.created` webhook | n/a (rare) |

All transactional emails carry `List-Unsubscribe: mailto:` headers
via `build_transactional_list_headers` (PR #4).

**The "your order has shipped" email is honest about timing.** It only
goes out once the order is *genuinely in transit* — `status == SHIPPED`
**and** a tracking number is present — never at voucher-mint. The
courier voucher mints during checkout (COD) or payment-success (online),
which sets the tracking number while the order is still PROCESSING; the
parcel only becomes SHIPPED later, when the carrier reports it moving
(ACS poll / BoxNow webhook). `send_shipping_notification_email` is
therefore dispatched from **two** events and self-gates on both
conditions: `order_shipment_dispatched` (tracking lands) and the
`order_status_changed`→SHIPPED transition. Whichever fires first finds
the other condition unmet and returns a no-op `True` *without* reserving
the `shipping_notification_email_sent` flag; the second one sends. This
also covers the admin who attaches tracking after flipping to SHIPPED
(or vice-versa). PROCESSING is never a customer email/toast — the
order-received (offline) / payment-confirmed (online) notification
already says "we're preparing your order". This is why placing a COD
order used to send three emails at once (received + "processing" +
premature "shipped"); now it sends only the confirmation.

### 6.2 Locale handling

- `Order.language_code` captured at create time
  (`OrderService._seed_language_code` reads
  `django.utils.translation.get_language()` — the request language
  `core/middleware/locale.py: RequestLanguageMiddleware` activates from
  the storefront's `X-Language`), so an order's emails and invoice come
  in the language the customer shopped in.
- A refused create answers 400 with a stable `error.type`
  (`order/enum/create_error.py: OrderCreateErrorType`); `detail` and
  `cart` are display text in the request's language, never a contract.
  A cart that fails `validate_cart_for_checkout` is `insufficient_stock`
  or `cart_invalid` (`CartNotReadyError`), and a hold that lapsed between
  the cart check and its conversion is `reservation_unavailable`.
- Every email task wraps render in
  `translation.override(get_order_language(order))`.

### 6.3 Customer-notification suppression on chained transitions (PR #7)

Three call paths chain `update_order_status` calls back-to-back.
Without suppression the customer would see two near-identical
emails / toasts within ms:

1. Carrier `_apply_order_status_transition` → DELIVERED → `maybe_advance_to_completed(silent_for_customer=True)` → COMPLETED.
2. `handle_payment_succeeded` → PENDING → PROCESSING (then `send_order_confirmation_email` covers the same ground).
3. `OrderService.add_tracking_info` admin path → PENDING → PROCESSING → SHIPPED.

`OrderService._suppress_customer_status_notifications(order, status)` pre-stamps two metadata flags:
- `status_update_email_sent_<status>` — short-circuits the email task's `_reserve_status_update_email`.
- `suppress_status_ws_<status>` — read by `handle_order_status_changed` to skip `notify_order_status_changed_live.delay`.

Internal state still flows: signal fires, `OrderHistory` rows logged, post-save handler runs. Only user-visible dispatches are skipped.

> Since `handle_order_status_changed` now categorically skips the
> customer email/toast for PENDING and PROCESSING (they're internal
> milestones), the PROCESSING suppression in paths 2 and 3 above is
> belt-and-suspenders. The **COMPLETED** suppression in path 1 is still
> load-bearing — COMPLETED *does* normally notify, so the
> DELIVERED→COMPLETED auto-advance must suppress it to avoid a duplicate
> right after the DELIVERED notification.

### 6.4 Live notifications (WebSocket toasts)

- `notify_order_created_live` — every order create.
- `notify_payment_confirmed_live` / `notify_payment_failed_live` — payment webhooks.
- `notify_order_status_changed_live` — meaningful status transitions only. Policy lives in `_ORDER_STATUS_COPY` (`order/notifications.py`): SHIPPED, DELIVERED, COMPLETED, CANCELED. **PENDING and PROCESSING are intentionally absent** — they're covered by the order-created / payment-confirmed toasts, so surfacing them again would be redundant.
- `notify_order_refunded_live` — refund signal.

There is no standalone "tracking available" toast: it fired at
voucher-mint (the same premature moment as the old shipped email) and
was redundant with the SHIPPED toast, so it was removed alongside the
email fix.

All WS notifications go through `notification.consumers.NotificationConsumer` and require auth; **guest orders silently get no live notification** (the email IS sent for guests since it goes to `order.email`).

## 7. Cancellation + refund flows

### 7.1 Cancel paths

- **Customer** (the owner, or a guest holding the order's uuid): `POST /api/v1/order/{id}/cancel` → `OrderService.cancel_order(order, reason, refund_payment=True, customer_initiated=True)`. The courier is asked first, see §7.1.1.
- **Store staff**: the same endpoint, with `customer_initiated=False` — the view takes it from `is_store_staff(request.user)`, never from `is_staff`.
- **Admin**: same `cancel_order` (`customer_initiated=False`) via the "Cancel selected orders and restore stock" action. The change form's `status` field is **read-only**: a form save wrote the column straight to the row, bypassing both `cancel_order` (no stock restore — prod orders 242 and 213, 2026-09-08) and the transition table in `update_order_status`. Every other transition is an action too (`mark_as_processing` … `mark_as_returned`, `mark_as_refunded`), routed through `update_order_status`. RETURNED and REFUNDED deliberately leave stock alone (§5.1).
- **Auto**: `auto_cancel_stuck_pending_orders` Celery beat — cancels online orders stuck in PENDING for >24h (`customer_initiated=False`).
- **Carrier-driven**: `AcsService._cancel_order_for_dead_shipment` (`customer_initiated=False`; the voucher is already canceled).

`customer_initiated` is a required decision for every caller that is not a customer: it defaults to `True`, so a caller that forgets to choose is held to the courier's answer (§7.1.1) rather than silently skipping it.

`cancel_order`:
0. Customer only: asks the courier to cancel the voucher before anything else (§7.1.1). No lock is held and no transaction is open for that call.
1. Locks order row.
2. Releases stock + reservations (`StockManager.increment_stock`, `release_reservation`).
3. Sets `status=CANCELED` (the save settles an unpaid `payment_status` to `CANCELED` — `Order.settle_unpaid_payment`), records `metadata['cancellation']`.
4. Staff / system only: cascades to the courier voucher via `cancel_attached_shipment` → `ShippingService.cancel_shipment` (PR #2 H). Records the dispatch outcome on metadata. A carrier rejection (e.g., voucher already in pickup list) does not stop the cancel: it is logged, kept in `metadata['cancellation']['shipment_cancel']['error']`, left as a staff-only `OrderHistory` NOTE (`Order canceled, but the courier did not cancel the parcel: …`), and the admin "Cancel selected orders" action raises an error message for it. For a customer cancel the courier already confirmed in step 0, so the outcome is recorded up front and the cascade (and the `order_canceled` signal's safety net) skips.
5. Optional refund via `refund_order` (when `refund_payment=True` AND `is_paid`).

**Don't bypass**: do not call `order.save(update_fields=['status'])` directly to cancel — you'll skip stock release + email + history. Always go through `OrderService.cancel_order`.

### 7.1.1 A customer cancel asks the courier first

An order stays PENDING / PROCESSING from the moment its voucher exists until a tracking poll sees the parcel move (voucher creation stores the tracking number but does not advance the status). In that window the courier may already hold the parcel, so a customer cancel is only accepted once the courier agrees:

1. `OrderService._ask_courier_to_cancel` re-reads the order's status (an order that no longer allows a cancel gets the usual status error and the courier is not asked), then calls `ShippingService.cancel_shipment` — the same carrier path a staff cancel uses (`AcsService.cancel_voucher`, `BoxNowService.cancel_shipment`). An order with no voucher has nothing to ask, and goes on.
2. If the courier confirms, the order cancels and refunds as usual, and `metadata['cancellation']['shipment_cancel']` is `{"attempted": true, "dispatched": <bool>}`.
3. If the courier does not confirm, `cancel_order` changes **nothing**: no status, no refund, no shipment change, no history row. Which answer the customer gets depends on why, and every case has the body `{"detail": <translated message>, "error": {"type": ...}}` (`OrderCancelErrorType`, `OrderCancelErrorSerializer`). Branch on `error.type`, never on `detail`.
   - **The courier refused** (an ACS voucher already on an issued pickup list, a BoxNow parcel past `NEW`, any other carrier business error): `ParcelWithCourierError`, 400 `parcel_with_courier`. The customer is told to contact the store about a return.
   - **The courier gave no answer** (unreachable, timed out, HTTP 5xx, an auth-layer rejection such as ACS's sporadic 406s, missing credentials, the same cancel already in flight): `CourierUnreachableError`, 503 `courier_unreachable`. Nothing is known about the parcel, so the customer is told to try again in a few minutes.
   - **Anything else** is a fault of ours, not the courier's answer: it propagates and the endpoint answers 500 through its error log.

   The split rides the carrier exception hierarchy: every carrier app's errors derive from `shipping.exceptions.CarrierError`, and the no-answer ones (`AcsRetryableError` and with it `AcsAuthError`, `AcsConfigError`, `BoxNowRetryableError`, `BoxNowAuthError`, `BoxNowConfigError`) also from `CarrierUnavailableError`. A new carrier gets the same answers by deriving its errors the same way.
4. The courier is asked before the order row is locked, so the order can move on while it answers; the locked re-check then refuses the cancel with the usual status error. The shipment was already canceled by then (at the courier, or locally when no voucher had been issued), so the order is live with no voucher behind it; any other failure of the locked cancel ends the same way. `OrderService._note_shipment_canceled_without_order` logs an ERROR (the logged-errors alert) and leaves a staff-only NOTE (`The customer asked to cancel this order and its shipment was canceled, but the order itself was not canceled (its status is …)`) telling staff to create a new voucher. An order another request canceled in the meantime needs neither.

Both carriers' cancel is idempotent on an already canceled shipment, so a customer who retries after a cancel that stopped half way is not refused (`BoxNowService.cancel_shipment` returns on a CANCELED parcel, as `AcsService.cancel_voucher` already did).

**`canBeCanceled`** (order list and detail) is `Order.can_be_canceled_by_customer`: the status rule (`Order.can_be_canceled`) AND `ShippingService.can_cancel_shipment(order)`. The second half reads local rows only, never the carrier: `AcsService.can_cancel_voucher` (false once the voucher is on a pickup list, `AcsShipment.pickup_list`) and `BoxNowService.can_cancel_shipment` (false once a parcel with a `parcel_id` is past `NEW`). These are the exact guards the carriers apply before they call the courier, so the flag and the cancel cannot drift; `ShippingCarrierInterface.can_cancel_shipment` defaults to `True` for a carrier with no cancel rule. A `True` is not a promise — the cancel request still asks the courier. `Order.can_be_canceled` (status only) stays the rule for staff and system cancels. The order list has no `canBeCanceled` filter: a status-only filter beside a carrier-aware field would list orders whose own flag says no. `OrderQuerySet.for_list()` selects `acs_shipment` / `boxnow_shipment` so the list reads the flag without a query per order.

### 7.2 Refund paths

- **In-app**: `OrderService.refund_order(order, amount=None|Money, reason)`. Reached from `cancel_order(refund_payment=True)` on a paid order (always `amount=None`, everything still refundable) and from the staff **"Refund (amount, reason)"** action on an order (below). A refund is as many calls as it takes: `refund_order` can be called again until nothing is left.
- **Stripe dashboard**: webhook `charge.refunded` → `handle_stripe_charge_refunded` (§4.2).
- **Viva dashboard**: webhook 1797 → `_handle_reversal_created` (§4.3). The same webhook also reports the refunds the in-app path makes; they are already on the ledger and add nothing.

Every path ends in `PaymentLedger.record_refund` (§7.3), so there is one rule for REFUNDED versus PARTIALLY_REFUNDED, and one `order_refunded.send` per REFUND row recorded → `handle_order_refunded` → email + WS notification + Meta Refund event. The email is due while the ledger holds more refunds than the emails already sent cover (`metadata["refunds_emailed"]`, set after a successful send), so each refund of an order tells the customer and a repeat of one signal sends nothing; it replaces the single boolean the first refund used to set, which silenced every later one.

**The staff action** (`OrderAdmin.refund_payment`, a dialog on the order's detail page) is offered for orders in `RETURN_HANDLING_STATUSES` (DELIVERED, COMPLETED, RETURNED: where a return, the EU 14-day right of withdrawal, is handled; before delivery a cancel refunds the order) to staff holding the order's change permission. It asks for an amount (defaulting to what the ledger says is still refundable) and a reason, calls `refund_order`, and reports the service's own refusal (nothing left, more than the remainder, not paid through a provider) or its failure to the staff member. It writes an admin log entry (who refunded what and why: the ledger row keeps the reason, and a staff identity cannot be a foreign key into a tenant schema). It changes neither the order status nor the stock: moving the order to RETURNED / REFUNDED stays the "Mark as ..." actions, and restocking returned units is `POST order-items/<id>/restock` (below). The read-only ledger is the order page's "Payment Transactions" tab.

**`POST /api/v1/order-items/<id>/restock`** (staff; it was `.../refund`) puts returned units of one line back in stock and records them on the line (`refunded_quantity`, `is_refunded`). It moves NO money and says so: what the customer is paid back is decided at order level, because a line's snapshot price is not what was paid for it (tax, shared discounts, shipping). It used to answer "Refund processed successfully" with a `refunded_amount` for money that never moved.

**What `refund_order` refunds comes from the ledger, not from the order.** `Order.paid_amount` is the amount owed when the order was created and is never consulted. Under the order row lock it reads what is left (captured minus refunded, payment by payment) and:

- refuses an amount that is not positive, and an amount above the remainder (`Refund amount (X) cannot exceed the refundable amount (Y)`), before any provider is called; a repeat refund that finds nothing left raises `This order has already been refunded.`; an order the ledger holds no provider payment for raises `This order was not paid through a payment provider...` (cash on delivery, a gift card, a hand edit);
- plans the refund payment by payment, oldest first: an order paid twice holds two CHARGE rows and a refund larger than one is split across them, one provider call each, one REFUND row each;
- calls the provider for each slice with `idempotency_key=f"order-{order.pk}-refund-{n}"`, `n` being the refund count the ledger holds plus one, counted under the same lock;
- records each refund the provider accepted as it goes, and signals it, so a failure on the second payment keeps (and has announced) the first (the response is then `(False, ...)` with that failure, and the refunded part is on the ledger);
- for an order whose capture the ledger never recorded (it settled while the previous release was serving, or was created from an already-succeeded intent whose event never reached it), reads the capture from the provider once and records it (`PaymentLedger.ensure_charge`); a capture the provider cannot confirm refuses the refund (staff NOTE, nothing sent) instead of leaving money returned and the order unbooked.

**Stripe's idempotency key** (docs.stripe.com/api/idempotent_requests): Stripe keeps the first answer for a key for at least 24 hours and answers a repeat of the same request with it instead of refunding twice; reusing a key with different parameters is an `IdempotencyError`. So an attempt whose outcome is unknown (the call timed out, or our transaction rolled back after Stripe accepted) is retried under the key it already used, because it left no ledger row and `n` did not move: Stripe returns the refund it made and the retry records it. Two limits follow and are accepted. After such an attempt, asking for a DIFFERENT amount reuses the key and is refused by Stripe. And an attempt Stripe ANSWERED with an error (insufficient balance, say) keeps that answer for its key for up to 24 hours: refund it in the dashboard instead, and the webhook records it.

**Stripe refund statuses** (docs.stripe.com/api/refunds/object): `succeeded` and `pending` are refunds Stripe took on (a part refund reports `succeeded` like a full one, so the status never says whether the order is fully refunded; the ledger's sums do), and are recorded, in REFUNDED or PARTIALLY_REFUNDED, both settled statuses a late `payment_intent.succeeded` cannot write over. `requires_action` waits on the customer and `failed` / `canceled` returned nothing: none is recorded, the order stays COMPLETED, and staff find Stripe's refund id and status in a NOTE on the order. The provider no longer reports a payment status at all (`refund_payment` returns `refund_id`, `amount`, `payment_id`). A refund Stripe accepted and later fails (`refund.failed`) is not reversed on the ledger: it has no kind for that, and nothing handles the event yet.

**The `order_refunded` contract** (`order/signals/__init__.py`). It carries `amount`, a `Money`: what THIS refund paid back, never absent (receivers that use it declare it as a required argument, so a sender that leaves it out raises). `order.payment_status` is already written (by `record_refund`) and says how much of the order is back: PARTIALLY_REFUNDED while part of the payment remains refundable, REFUNDED once nothing does. **Whatever hangs off the order's total is released only on REFUNDED, once, with the last refund**: the gift-card redemption (`giftcard.signals.handle_order_refunded_giftcard` → `GiftCardService.credit_refund`, the whole redemption) and the loyalty points (`loyalty.signals.handle_order_refunded_loyalty`, which is all-or-nothing and once per order). A partial refund is paid back by the provider first, so a slice of the redemption on top would hand back more than that refund was worth (a EUR 5 refund once put EUR 60 back on a gift card), and the one-credit-per-card ledger could not top the card up when the rest is refunded. The email, the toast, the history row and the Meta `Refund` event fire on every refund, partial Stripe dashboard refunds included (they used to be silent).

**A refund the provider did not make is not recorded.** `refund_order` returns `(False, response)` and changes nothing on the order: `payment_status` stays COMPLETED, no REFUND row is written, `order_refunded` does not fire. It leaves a staff-only `OrderHistory` NOTE (`Refund via <provider> was not processed: …`; `NOTE` is not a customer-visible history type). `cancel_order` still cancels and reports `refund_info = {"refunded": False, …}` with no provider text; the admin "Cancel selected orders" action raises an error message for it.

**Viva (`VivaWalletPaymentProvider.refund_payment`).** Cancel Transaction requires `amount` (cents), so the request always carries it, and so does every call now: the service always names an amount (a full refund is the ledger's remainder, never "ask Viva what it captured"). HTTP 2xx is not success: Viva reports refusals in a 200 body, so only `Success` true with `ErrorCode` 0 counts, and the body's `TransactionId` (the refund transaction) is the ledger's reference for the refund. `DELETE /api/transactions/{id}` has no idempotency parameter (Viva's `idempotencyKey` is on the `/acquiring/v1/transactions/{id}` route), so none is sent; Viva refuses a refund above what is left on the payment, and the ledger's unique constraint on the refund transaction id keeps it from being recorded twice. Before #134 the amount was omitted and the body ignored, so every in-app Viva refund booked REFUNDED while Viva kept the payment at `F`.

### 7.3 The payment ledger (`PaymentTransaction`)

`order/models/payment_transaction.py`, written only through `order/payment_ledger.py` (`PaymentLedger`). Append-only, like `GiftCardTransaction`: a row is never updated or deleted, and `Order` → ledger is `PROTECT` (deleting an order for real has to remove its ledger first, as the demo reset does).

| Column | Meaning |
|---|---|
| `order` | the order (`payment_transactions`) |
| `kind` | `CHARGE` (a provider captured money), `REFUND` (a provider paid it back), `COD_COLLECTION` (a courier collected cash). No chargeback kind yet: a dispute is not a loss until it closes against the merchant, and no handler writes one. |
| `amount` | always positive; the kind gives the direction |
| `provider` | `stripe`, `viva_wallet`, or the courier's `acs_cod` / `boxnow_cod` |
| `provider_reference` | the provider's own id: PaymentIntent, Viva transaction, refund id, voucher / parcel (a Stripe dashboard refund has no id in `charge.refunded`, so it carries the event id) |
| `charge` | on a REFUND, the CHARGE it reverses (an order paid twice is refunded payment by payment); null otherwise (a DB check enforces both) |
| `description` | the reason given for a refund |
| `created_at` | when it was recorded |

`UNIQUE (provider, provider_reference, kind)` is the idempotency of every writer: a delivery repeated by the provider records nothing the second time (`record_*` return `created=False`). Writers (all under the order row lock): `OrderService.handle_payment_succeeded`, `handle_stripe_checkout_completed`, `_handle_payment_created` (CHARGE); `AcsService._mark_cod_order_paid_if_pending`, `BoxNowService._mark_cod_order_paid_on_delivery` (COD_COLLECTION); `OrderService.refund_order`, `handle_stripe_charge_refunded`, `_handle_reversal_created` (REFUND, through `record_refund`).

Everything else is read from the sums: `PaymentLedger.captured` (CHARGE), `refunded` (REFUND), `refundable` (per payment, so it never counts cash), and **`refund_status(order)`, the one function that decides REFUNDED versus PARTIALLY_REFUNDED** (refunded ≥ captured). `record_refund` writes the derived status onto the order in the same step as the row, so no path decides it for itself and it cannot go backwards. `payment_status` stays a column (every filter, serializer and guard reads it); the ledger is what it is derived from after a refund.

**`Order.metadata["refunds"]` is gone**, and with it the Stripe `charge.refunded` `webhook_processed_*` flag and `PaymentStatus.after_refund`. `PaymentProvider.captured_amount` is gone too: providers refund an amount they are given.

**Migration `0067_backfill_payment_transactions`** wrote rows for the orders paid before the ledger existed, from what the rows prove and nothing it would guess: CHARGE (amount `paid_amount`, which checkout verified against the provider) for Stripe and Viva orders, COD_COLLECTION for `acs_cod` / `boxnow_cod` orders, REFUND for `metadata["refunds"]` entries that name a provider refund id and an amount (the old `"full"` marker means whatever the payment still held; a Stripe `charge.refunded` entry's cumulative total becomes one row for the part the named refunds do not cover). It could not reconstruct: the amount the provider actually captured (it uses `paid_amount`); any payment but the last of an order paid twice under two Viva codes (`payment_id` holds only the latest); a COD collection's courier reference (the row is keyed `order-<id>`); who refunded (a staff identity cannot be a foreign key into a tenant schema); a refund no provider id was kept for (the four production Viva orders refunded "in-app" before #134 show REFUNDED but the money never left Viva: they get their CHARGE and no REFUND, so the ledger shows the payment refundable, which is true); and anything pods of the previous release wrote while it ran (a payment recorded in that window is read from the provider when a refund first needs it). `payment_status` was left alone.

## 8. Critical invariants

These exist for a reason. Don't undo any without first re-reading
the linked memory note or the originating PR's commit message.

| Invariant | Anchor |
|---|---|
| Backend uses `psycopg` pool with `CONN_MAX_AGE=0` under ASGI | `project_db_pool.md` |
| `ShippingService.dispatch_create_shipment_task` wraps in `transaction.on_commit` | `project_shipping_dispatch_on_commit.md` |
| No carrier mint while `Order.awaits_online_payment` (`ShipmentAwaitingPaymentError`) | §5 above |
| A payment-success handler checks `is_payment_after_cancel` BEFORE its settled-state guard | §2.2 above |
| Order `status` changes only through admin actions / `OrderService`, never the change form | §7.1 above |
| Every checkout-created order since `0059_orderattribution` has exactly one `OrderAttribution` row (`direct` when there was no signal); for a checkout order a missing row means "older than attribution", never "direct"; `source_type=agent` only with the gateway's `X-Internal-Gateway` secret | §3.4 above |
| An issued tax document never re-reads the catalogue's VAT: invoices and myDATA split each line at `OrderItem.vat_rate`, snapshotted at creation, and tax shipping and the payment fee at the rate(s) of the goods (`order/taxation.py`); `Invoice` totals and the myDATA `invoiceSummary` come from the same lines | §3.5 above |
| The default cache fails open on a Redis outage (reads miss, writes no-op — `core/caches.py`), but never for keys under `STRICT_KEY_PREFIXES`: the `strict:` throttle namespace, allauth's rate limits, TOTP replay protection, the IdP's single-use codes and WebSocket tickets. The checkout, payment, coupon and gift-card throttles (`core/api/throttling.py`, `fail_closed = True`) therefore DENY with 429 during an outage, while browsing/search/cart throttles and DRF's default `anon`/`user` budgets allow | `core/api/throttling.py` module docstring |
| The tenant resolve payload and domain set are invalidated by `Tenant.cache_generation`, never by a Redis delete: it is part of their keys, and `Tenant.save`, every `TenantDomain` write and the folded pay-way / agent-setting writes bump it in the database inside the write's transaction. A delete during an outage is a fail-open no-op, so purge-by-delete left the pre-write payload served for up to its TTL; strict keys would have made the READS raise, and resolve is on every storefront request | `tenant/cache.py` module docstring |
| The nightly demo reset returns the published demo gift card (`GIFT_CARD_CODE`) to its issued value with one `ADJUST` ledger row, so the ledger sum stays the balance and every guest spend stays on the card's history; only on `is_demo` tenants, and silently (no mail, no dispatch) | `devtools/demo_account.py: _restore_gift_card` |
| ACS voucher mint uses 3-phase claim → API → persist with 300s TTL | `project_acs_voucher_orphan_prevention.md` |
| `Order.objects.filter(pk=...).values(...).first()` — NOT `refresh_from_db(fields=...)` | `project_order_state_machine_invariants.md` |
| `_suppress_customer_status_notifications` on chained transitions | `project_order_state_machine_invariants.md` |
| Webhook payment_status writes never regress a SETTLED state (`SETTLED_PAYMENT_STATUSES` = COMPLETED/REFUNDED/PARTIALLY_REFUNDED/CANCELED). Stripe/Viva events are unordered + may duplicate, so `handle_payment_failed`/`_handle_payment_failed` skip when already settled, and `handle_payment_succeeded`/`_handle_payment_created` skip when already REFUNDED/PARTIALLY_REFUNDED/CANCELED. Reversal (COMPLETED→REFUNDED) is left intact. | `order/enum/status.py` `SETTLED_PAYMENT_STATUSES` |
| `order_refunded` always carries `amount`, and the gift-card redemption and loyalty points are released only when `payment_status` is REFUNDED (the last refund), never per partial refund. A Viva 1797 is applied from the REFUND transaction (`F`, refund/void type, negative amount), not from a REFUNDED status that Viva never reports | §4.3, §7.2 |
| Money moved for an order is recorded on the append-only `PaymentTransaction` ledger, once, keyed on the provider's own id (`UNIQUE (provider, provider_reference, kind)`). What can still be refunded is read from it under the order row lock, and `PaymentLedger.refund_status` is the ONE place that decides REFUNDED versus PARTIALLY_REFUNDED: no path writes either status for itself, and none re-derives it from `Order.paid_amount` (the amount owed at creation) or from a provider's refund `status`. A refund is as many `refund_order` calls as it takes, never past the remainder | §7.2, §7.3 |
| A Stripe refund carries `idempotency_key=order-<id>-refund-<n>`, `n` counted from the ledger under the order lock. A refund the provider did not take on (`requires_action`, `failed`, `canceled`) records nothing and never moves the order to PROCESSING | §7.2 |
| The "shipped" email/toast fires only at genuine SHIPPED (status==SHIPPED **and** tracking present), never at voucher-mint; PROCESSING is internal-only (no customer email/toast). `send_shipping_notification_email` self-gates. | §6.1 above |
| Admin-WYSIWYG fields in emails render `\|safe` (.html) / `unescape(strip_tags())`+`\|safe` (.txt — Django autoescapes .txt too) | `CLAUDE.md` "Transactional email rendering" |
| ACS COD numeric fields use Greek-locale (comma decimal) | `project_acs_cod_locale.md` |
| Don't import from `'#shared/...'` in app/ or server/ | `feedback_no_shared_imports.md` |
| Don't override generated Zod / OpenAPI types in Nuxt | `feedback_no_local_schema_overrides.md` |
| OpenAPI regen workflow after Django serializer changes | `project_schema_regen.md` |

## 9. Common task playbook

### 9.1 Adding a new order email

1. Create the template under `core/templates/emails/order/`.
2. Add a Celery task in `order/tasks.py` mirroring `send_payment_failed_email`:
   - Idempotency reservation helper (`_reserve_<task>_email`).
   - `translation.override(get_order_language(order))` block.
   - `EmailMultiAlternatives(...)` with `headers=build_transactional_list_headers(list_id="<kind>")`.
   - Catch exceptions; on permanent failure, release the reservation.
3. Wire from a signal handler in `order/signals/handlers.py` via `transaction.on_commit`.
4. Add a regression test in `tests/integration/order/test_signals.py`.

### 9.2 Adding a new order status

1. Add the value to `OrderStatus` in `order/enum/status.py`.
2. Update the transition table in `OrderService.update_order_status`.
3. Add a Greek translation in `locale/el/LC_MESSAGES/django.po`, run `compilemessages`.
4. Add the matching `_ORDER_STATUS_COPY` entry in `order/notifications.py` for the WS toast.
5. Create email templates under `core/templates/emails/order/order_<status>.{html,txt}`.
6. Add a state-machine test in `tests/integration/order/test_state_machine.py`.

### 9.3 Surfacing a new field on the order detail API

1. Add the field on `OrderSerializer` (list shape) or `OrderDetailSerializer` (detail-only) in `order/serializers/order.py`. Use `extend_schema_field` so spectacular emits it.
2. `uv run python manage.py spectacular --color --file schema.yml`.
3. In Nuxt: `pnpm generate:schema && pnpm openapi-ts && pnpm sync:schema`.
4. Type-safe consumption in `app/pages/account/orders/[id].vue`.

### 9.4 Reconciling state after a manual prod fix

If you ever touch order metadata or status directly via shell
(`order.save(update_fields=[...])`), you've bypassed signals.
Recover by:
1. Note exactly which fields you changed.
2. If status was changed: roll it back to `PENDING` and call
   `OrderService.cancel_order(...)` so the canonical path fires.
3. For voucher mint mishaps, follow the order-47 recovery script
   in `commit 05208050`.

### 9.5 A customer email carries the wrong subject or wording

Before touching a template, check the DB translation overlay. Every
non-empty `core.Translation` row is written over the compiled `.po` in
every pod and worker (`core/rosetta_storage.py:apply_db_overlay`), so a
stale row wins over a correct catalogue with nothing on disk to show
for it — order #281 (2026-09-12) got "Η Παραγγελία Παραδόθηκε" as its
*order received* subject from an April import row. A fresh
`manage.py shell` never applies the overlay, so it will insist the
catalogue is fine.

1. The rendered subject is in the worker log line
   `Order confirmation email sent for order #N (<template>): '<subject>'`
   and in the order's history note — confirm what was actually sent.
2. `manage.py translation_overlay_audit -l el` lists every row that
   overrides the `.po` (`custom`) with both values side by side.
3. Drop the stale ones — `--drop-ids …` for a handful, `--drop-before
   YYYY-MM-DD` for a bulk import — and `--prune` the rows that equal
   the `.po`. Every write bumps the version key; no restart needed.

## 10. Audit history (PRs #1–#8)

All landed 2026-04-30 → 2026-05-01.

| PR | Theme | Scope |
|---|---|---|
| 1 | Race conditions | COD voucher-mint advance, payment handler row locks, on_commit regression test |
| 2 | State machine completion | Cancel cascade to courier, charge.refunded webhook, COD reconcile flips payment_status, DELIVERED→COMPLETED auto |
| 3 | UX polish | PaymentStatus translations + display, COD alert suppression, cancellation surface, language_code capture |
| 4 | Email gaps | Shipping notification email wiring, List-Unsubscribe transactional headers |
| 5 | Audit trail | HistoricalRecords on shipments, is_online_payment list serializer, dispute_notification templates |
| 6 | Concurrency hardening | Voucher-mint TTL 90→300s, ACS DELIVERED→COMPLETED tests |
| 7 | Notification dedup | Suppress duplicate customer emails + WS toasts on chained transitions |
| 8 | Last gap | Refund confirmation email |

For the *why* behind each item, read the commit message — they're
written specifically to survive a `git log --grep="^fix"` browse
six months from now.
