# Order / Payment / Shipping / Notification System

**Reference for anyone (Claude included) maintaining this surface.**
Keep this file synchronised when invariants change. Cross-references
are file paths + line numbers; the system has enough load-bearing
"don't undo this" pieces that drift here is expensive.

Last refresh: 2026-10-29 (the order emails' subject and context are built once in `order/emails.py` for the sender and the admin preview, §6.1; marketing mail honours the account-level opt-out the one-click link records, §6.5; the store's contact/feedback notifications and the allauth account emails, §6.6 / §6.7; before that 2026-10-18: value-based refunds and the negative-balance setting, §7.2; refunds claim, call and record with no transaction open across the provider, `RefundAttempt`, Stripe `refund.failed` reversal, §4.2.0 / §7.2; before that 2026-10-13: refunding collected cash on delivery, §7.2; before that 2026-10-12: the myDATA retail document lifecycle: a receipt at shipment, a credit document per refund, no platform invoices, §7.4; before that the payment ledger: partial refunds, §7.2 / §7.3; before that 2026-09-30: every payment start refuses a non-PENDING order, a change during the provider call wins with 409, webhook history/emails only for an applied event, BoxNow webhook 400 for a non-object, non-UTF-8 or too-deep body; before that: order acquisition source, admin payment-method column, then no mint before payment, payment-after-cancel ahead of the settled guard, admin status read-only, ACS pickup-list `blocked_unprinted`, postcode refusal). See git log for changes since.

## 1. Overview

Three orthogonal axes describe every order in the system:

| Axis | Values | Meaning |
|---|---|---|
| `Order.status` | `PENDING`, `PROCESSING`, `SHIPPED`, `DELIVERED`, `COMPLETED`, `CANCELED`, `RETURNED`, `REFUNDED` | The fulfilment state. |
| `Order.payment_status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `REFUNDED`, `PARTIALLY_REFUNDED`, `CANCELED` | The financial state. **Decoupled from `status`** — important for COD where the parcel can be SHIPPED while payment is still PENDING. |
| `Order.pay_way.settlement` | `online`, `courier_cash`, `carrier_terminal`, `offline_transfer` | How the money changes hands. Drives which create path runs (online → webhook completes payment; the rest → collected later) AND which carriers may serve the order — a carrier declares the settlements it can physically perform (`supported_settlements`). The pay way's former `is_online_payment` / `requires_confirmation` booleans are gone (`pay_way` 0034 took them out of the model, 0035 dropped the columns). The order's `isOnlinePayment` is a different field, computed from `settlement`. |

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
- `→ REFUNDED` is refused (`RefundOutstandingError`, in `update_order_status`, so the admin "Mark as refunded" action and every other caller) while `PaymentLedger.refund_open(order)` (refundable money, or a paid provider order whose capture was never recorded): the status says the customer was paid back, and the loyalty reversal and the "your refund has been processed" status email follow it. Refund first (§7.2); an order with nothing collected (a refused parcel that came back) closes freely. An order paid entirely with gift cards and points has no provider payment, and it is open until its stored value has gone back (`refund_open` counts it): staff refund it through the Refund action (value-based, §7.2), which credits the cards and restores the points, and only then can it be closed as REFUNDED.
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

`Order.clean()` refuses a COMPLETED payment with `amount_due` 0.00
unless deductions cover the whole total (`Order._payment_consistency_errors`)
— the admin form edits both fields. It is not a `CheckConstraint`: the
total is a sum over the order's lines.

### 2.3 The follow-up flag (`order/follow_up.py`)

A shopper who ordered N calendar days ago and still has no parcel is about
to write in. The admin flags that order. It is computed, never stored: no
column, job or email, and the flag disappears by itself when the order is
delivered, completed, returned, canceled or refunded.

An order needs follow-up when **all** of these hold:

- it was placed on a store-timezone day at least `ORDER_FOLLOW_UP_DAYS`
  days ago (store setting, default 7, `0` = off). Whole calendar days: an
  order placed on day D is flagged from 00:00 on day D+N, not 24 h × N
  after it was placed (`follow_up_cutoff`);
- its status is PENDING, PROCESSING or SHIPPED
  (`OrderStatus.get_status_groups()` `needs_processing` + `in_transit`);
- it is not an online order still waiting for its payment
  (`OrderQuerySet.awaiting_online_payment()`: `pay_way.settlement=online`
  and `payment_status` not in `SETTLED_PAYMENT_STATUSES`). An unpaid card
  order is an abandoned checkout, not a parcel to chase. Do **not** use
  `Order.awaits_online_payment` for this: it is also true for an online
  order that is refunded, partially refunded or canceled.

The rule is one queryset, `OrderQuerySet.needing_follow_up(now)`; the
changelist's `with_follow_up()` annotation (`needs_follow_up`, no query
per row), the "Needs follow-up" filter (`follow_up=yes|no`) and the
`follow_up_orders` dashboard counter all read it, so they cannot disagree.
The changelist shows a blue "Follow up" badge beside the status; the
change page adds a read-only line with the reason ("Not shipped yet" for
PENDING/PROCESSING, "In transit" for SHIPPED), since when, and the
carrier's last event (`AcsShipment` / `BoxNowShipment.last_event_at`). The
counter is a row of the dashboard's "Needs attention" list linking to the
filtered list; the Orders sidebar badge keeps the pending count.

## 3. Order creation paths

Two entry points in `order/services.py` (the legacy `OrderService.create_order`
— dead code with zero production callers — was removed 2026-08-24):

### 3.1 `create_order_from_cart` — payment-first / online
- **Caller**: `OrderViewSet.create` for an intent-based ONLINE pay way (Stripe)
  whose request carries `payment_intent_id`. Every client reaches it through
  `POST /api/v1/order`: the storefront, and the agent gateway's UCP/ACP
  checkout (which sends no intent, so it takes §3.2). Nothing else calls it.
- Sets `status=PENDING`, `payment_status=<provider-returned>`.
- **Does NOT** dispatch courier task here. Online payments defer
  the dispatch to the payment-success webhook (PR #1 commit
  `59527a87` — see `project_shipping_dispatch_on_commit.md`).
- **The provider is asked before the transaction opens.**
  `_retrieve_payment_intent` runs `get_payment_status` (for Stripe
  `PaymentIntent.retrieve` plus the dj-stripe sync, an HTTP round trip) and
  refuses an intent that is not PENDING / PROCESSING / COMPLETED. Inside the
  transaction that call held the cart row and every live automatic
  `Promotion` row, which every concurrent checkout (Viva and COD included)
  also locks, for as long as Stripe took to answer. Nothing is locked while
  the provider answers, and `OrderViewSet.create` is deliberately NOT
  `@transaction.atomic`: a view-level block would put the call back inside a
  transaction, idle on a connection that `idle_in_transaction_session_timeout`
  kills. `_create_order_for_verified_intent` is the one transaction; it
  re-compares only what depends on server state, the intent's amount and
  currency against the total it computes under lock (Step 3, which also
  prices loyalty and gift cards under their own locks). Order of checks
  therefore changed: a cart that cannot be checked out costs one provider
  read first, with no lock.
- **The intent must be this cart's** (`order/payment_binding.py`,
  `OrderService._verify_intent_binding`). An intent proves that money moved,
  not what for, so each intent is tied to its purpose. Every PaymentIntent we create now carries server-set metadata
  (`purpose` = `cart` / `order` / `giftcard`, `subject` = the cart, order or
  purchase uuid, `tenant_schema`), written by `StripePaymentProvider`
  (`process_payment`, `create_checkout_session` and `confirm_delegated_payment`
  all require it; `PayWayService.process_payment` sets it last, so a
  caller's `payment_data` cannot). Payment-first creation refuses, as
  `payment_verification`, an intent whose metadata is not `cart` + this
  cart's uuid + this tenant, and any intent id a `GiftCardPurchase` holds.
  The refusal names the key that differs, never the uuid. An intent created
  before this shipped carries no metadata and is refused too. The webhook
  side is the mirror: `payment_intent.succeeded` issues no gift card for an
  intent an order also holds.
- **Idempotent per payment intent.** `Order.payment_id` is unique
  (`order_payment_id_unique`, a partial unique index over non-empty ids), and
  `_order_created_for_payment` looks the intent up twice: first WITHOUT a lock,
  before the provider is asked, so a repeat is answered from what is stored
  and does not depend on the provider being reachable (a retry after a timeout
  is when it may not be); then right after taking the cart row lock, BEFORE
  cart validation, which is what serialises two first submits of one cart. The
  order it produced for THIS cart is returned as it is (the view answers `201`
  again, the same response the first submit got), so a double click, a retry
  after a timeout or Back-and-resubmit while the hosted payment is open yields
  one order and one stock decrement. Both run before cart validation because
  the first order has already taken the stock (and, once paid, emptied the
  cart), which a repeat would otherwise be refused for. An intent that belongs
  to another cart, or to a canceled or deleted order, is not a repeat:
  `PaymentVerificationError` (400 `payment_verification`) — answering would
  hand one shopper another's order, creating would take a payment already
  committed elsewhere. The cart lock does not serialise two DIFFERENT carts
  submitting one intent: both can miss the lookup, and the unique index then
  rejects the second insert. `create_order_from_cart` reads the violated
  constraint's name off the database error (`PAYMENT_ID_UNIQUE_CONSTRAINT`;
  any other integrity error keeps its generic `invalid_order_data`) and
  answers it the same way, `payment_verification`.
  Limits: the repeat must reach the service (the view validates the cart
  first, so a repeat after the first order emptied the last units, or after the
  cart was deleted on payment, is refused there as before, with no duplicate);
  and a repeat is answered with the first order even if the body now carries a
  different address, because the intent was paid for that one.
- **Product locks are taken in ascending pk order, all in one statement, before
  the first row that references a product is inserted.** The cart-item query
  locks only the cart lines (`of=("self",)`; the join used to lock the products
  too, in the cart's line order), then
  `OrderService._lock_order_products` → `StockManager.lock_products` locks
  every product the order touches (cart lines, the cart session's reservations,
  free-gift products) once, `ORDER BY` pk. Two carts holding the same products
  in different line orders used to take them in opposite orders, which
  PostgreSQL resolves by aborting one checkout. It must precede the
  `OrderItem` inserts because each takes `FOR KEY SHARE` on its product, which
  conflicts with `FOR UPDATE`: two checkouts that insert first and lock after
  wait on each other whatever order they then ask in. The per-product locks in
  `StockManager` that follow re-lock rows already held and cannot block.
  `cancel_order` (a restore takes one lock per product), the bulk discount
  admin actions and the guest→user cart merge follow the same rule;
  `reserve_stock` is not affected, each call being its own transaction.
  Not covered: an admin edit that changes the quantity of several
  `OrderItem` rows in one request (`handle_order_item_post_save` →
  `StockManager.adjust_stock`) locks their products in form order.

### 3.2 `create_order_from_cart_offline` — order-first / COD / Viva
- **Caller**: Nuxt checkout for offline pay-ways + Viva redirect flow, and the
  agent gateway's UCP/ACP checkout.
- Sets `status=PENDING`, `payment_status=PENDING`.
- Same lock order as §3.1 (cart row, then promotions, then products in pk
  order before any `OrderItem` insert). There is no provider call here.
- **Not idempotent.** A hosted-payment order (Viva) keeps the cart, so a
  repeat submit creates a second PENDING order that takes its own stock until
  its payment hold ends (§7.1.0). Reusing the open order needs a key over
  the whole request (cart lines and prices, pay way, address, loyalty points,
  gift-card codes) and has to run before BOTH stock validations (the view's and
  the service's: the open order already took the stock), and what the first
  creation consumed (the coupon, the gift-card balance, the loyalty points) is
  not recoverable from the inputs. It is a separate change, not a lookup.
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
- **Countries and regions are public, shared rows; what uses them is not.**
  `country` and `region` are in `SHARED_APPS` only (their tables exist
  once, in `public`; a store's `search_path` falls through to them), and
  `tenant.role_scopes.REFERENCE_DATA_APP_LABELS` gives stores `view` on
  them and nothing else. Orders, saved addresses, shipping rates and
  pay-way exclusions are in each store's own schema. So deleting one is
  refused when ANY store's rows point at it
  (`tenant/reference_guard.py`): the admin lists the stores on its
  confirmation page (`tenant.deletion.PublicReferenceDataDeletionMixin`)
  and a `pre_delete` receiver in `tenant/signals.py` refuses every other
  path. A country is also refused when a store uses one of its regions,
  because the regions go with it. Soft-deleted orders count (the foreign
  key is still there); simple-history rows do not. Deleting a row nobody
  uses works from the platform admin. Both ends of a store's use are
  found from the models' own reverse relations, hidden ones included, so
  a new model with a foreign key to `Country` is covered without a list
  to update.

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
  and the dashboard's "Where orders come from" card
  (`admin/dashboard/store/queries.py: order_sources`, drawn by
  `widgets.py: StoreOrderSources`; the dashboard's selected period, top
  five as shares plus "Other", orders without a row left out).
  `source_label()` turns a stored `(source, source_type)` into the name
  staff read.
- **Trust.** Every input but the agent protocol is shopper-controlled,
  so the row is analytics only and never an authorisation or pricing
  signal. The agent source type is the one verified claim (see Input).
- **Cart first touch (`cart.CartAttribution`).** The cart records where
  its shopper came from too, because that is often not where they
  checked out ("checkout from Google, cart started from Instagram").
  `OrderAttribution` and `CartAttribution` share their columns through
  the abstract `AcquisitionSourceModel` (`order/models/attribution.py`).
  `POST /cart/item` takes the same optional write-only `attribution`
  object as order create (`OrderAttributionInputSerializer`, the same
  shape and rules, `agentProtocol` believed only with the gateway
  secret). `CartAttributionService.capture` (`cart/services.py`) is the
  one entry point, called BEFORE the line is written, by every path that
  can take a cart from no lines to some: the add itself (a new cart, or a
  refill after the shopper emptied it), `OrderService.reorder_to_cart`
  (the request's own signals: a reorder carries no capture) and the
  guest-to-account `merge_carts`. An empty cart takes the touch,
  replacing an earlier fill's; a cart holding lines keeps its own, so a
  second add never overwrites it; the merge carries the guest cart's
  touch over only when it is earlier than the account cart's (or the
  account cart has none). An add with no `attribution` object records
  nothing, so the agent gateway's carts and carts from before
  `0015_cartattribution` simply have no row (a missing row is "unknown",
  never "direct"). The cart response does not expose it; the storefront
  never reads it.
- **Cart source on the order.** The cart is deleted once its lines
  become an order (`_clear_cart_for_order`), so both creation paths copy
  the cart's first touch into `OrderAttribution.cart_source_type` /
  `cart_source` (`OrderAttributionService.record(..., cart=cart)`), next
  to the checkout source on the same row. Empty strings mean the cart
  had none recorded, not "direct". Not exposed on the order API.
- **Cart admin.** Carts show an "Order source" column and filters
  (`attribution__source_type`, `CartSourceFilter`), built on the same
  `AcquisitionSourceFilter` (`admin/filters.py`) as `OrderSourceFilter`,
  and a "Checkout" column / filter ("Checkout started, awaiting
  payment: order #N", linked) for a live cart whose checkout already
  created an order in `awaiting_online_payment_q`. The order is found
  through `metadata["cart_snapshot"]["cart_uuid"]` in one correlated
  subquery on the changelist queryset. The filter is named apart from
  the idle "Abandoned" cart type. The order list adds a "Cart source"
  column, filters (`attribution__cart_source_type`,
  `OrderCartSourceFilter`) and change-form fields next to the order
  source: conversion by cart source is read from the orders list.
- **Erasure.** Both rows store a channel name, a medium, a campaign, a
  referrer host (no path or query) and a landing path (no query), all
  capped. They hold no account identifier, but `source`, `medium`,
  `campaign` and `landing_path` are whatever the shopper's link carried,
  so a hand-typed link can put personal text in them; nothing scrubs
  that. Erasure treats them as follows. `CartAttribution` cascades with
  its cart, and so with the account. `anonymise_and_delete_user` does
  not touch `OrderAttribution`: the order is kept as the store's
  record, and its attribution row stays with it, `source`,
  `landing_path` and the copied `cart_source_type` / `cart_source`
  alike (`test_the_cart_source_goes_with_the_cart_and_the_order_keeps_its_own`
  pins this). Neither row is in the data export.

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
  admin hides the rate on the add page (`save()` snapshots it) and keeps
  it editable afterwards, so staff can correct a wrong snapshot, until
  the order's invoice carries a myDATA MARK; from then on it is
  read-only, because the registered document can no longer change. A
  line staff add through the API is priced like a checkout line: the
  product's VAT-inclusive `final_price`, or the price of the customer
  group that priced a wholesale order (`metadata["b2b_pricing"]`).
- **Nothing reads `product.vat` for an order again.** `order/invoicing.py`
  and `order/mydata/builder.py` get their lines from
  `order.taxation.order_vat_lines`, which reads `item.vat_rate`. Editing
  a `Vat` row, moving the product to another, or deleting the row (the
  FK is `SET_NULL`) changes neither the PDF nor the myDATA payload of an
  existing order. Regenerating a not-yet-registered invoice therefore
  keeps the original rates too. Lines that predate the column were
  backfilled (`0065`) from their product's VAT at deploy time, which is
  what the documents used before the snapshot existed.
- **Where a discount lands.** A monetary discount (promotions + the
  loyalty redemption) reduces the taxable value of the lines it was
  granted on, not of every line: a coupon for the 13% food category must
  not be booked against a 24% line. `PromotionEngine` reports it:
  `PromotionEngine._clamp` splits each applied promotion over the cart
  lines it covers (`_covered_weights`: the lines `_matching_items` counts,
  by value, or the units a buy-X-get-Y actually discounts, by price) with
  `order.discounts.allocate_capped`, the one largest-remainder rule (cent
  exact, no line pushed below what is left of it), and exposes the result
  as `AppliedPromotion.line_amounts` / `CartDiscountResult.line_discounts`
  (cart item pk -> amount). The cart preview (`Cart.vat_after_discount`),
  the payment-intent amount and order creation all read the same
  evaluation. Checkout (`OrderService._snapshot_line_discounts`, both
  paths) freezes each line's share on `OrderItem.discount_allocation`
  (the whole line, not per unit) exactly as it freezes `vat_rate`; the
  loyalty redemption is order-wide and is spread over the value left on
  every line (`spread_by_remaining_value`; the standalone
  `/loyalty/redeem` endpoint does the same on an existing pending
  order). Gift-card amounts are payment and never allocated; a staff-added
  line carries none; a free gift has no value to carry any.
  `order.discounts.discounted_line_gross` reads the snapshots and raises
  when they do not add up to the order's discount (capped at its goods): a
  document on such lines would bill a total the customer never owed. That
  is the state of an order created by the previous release during the
  rollout of the column, or one whose discounted line staff deleted.
- **Orders without a snapshot.** `order` 0072 adds the column
  (`db_default` 0, currency column added with the `docs/migrations.md`
  money recipe) and 0073 gives every existing discounted order the
  proportional allocation (`heal_discount_allocation` in
  `order/discount_snapshot.py`): a `PromotionRedemption` row records the
  promotion and its amount but not the lines, and the promotion may have
  been edited since,
  so proportional, the rule every existing document was issued under, is
  the only honest reconstruction. `Invoice` rows are never touched, and
  one with a MARK is frozen anyway. Orders the previous release creates
  while it still serves the rollout are not covered by 0073 (it already
  ran): run `manage.py heal_discount_allocation --all-tenants` once after
  the last old pod has exited (idempotent: an order whose lines carry any
  allocation is skipped). The invoice task fails loudly on such an order
  until then and retries.
- **Shipping and the payment-method fee.** Incidental costs the seller
  charges the customer belong to the taxable amount of the goods they
  accompany (VAT Directive 2006/112/EC art. 78(b); Greek VAT Code, Law
  5144/2024, art. 24(4)(a) — art. 19 of the repealed Law 2859/2000), so
  they take the goods' rate. A basket that mixes rates has no single
  rate for them: each charge is split over the rates in proportion to
  the value of the goods charged at each, i.e. after their discounts
  (`_rate_weights` over `discounted_line_gross`; AADE circular Εγκ.
  10/1987 Π.4336/3162: incidental costs follow "την αξία των αγαθών";
  units when the goods are free; largest remainder, so the shares add
  up to the cent) and every share is taxed at its own rate. A
  single-rate basket puts 100% on that rate. There is no order-level
  "shipping VAT rate" to store: the split is a function of the
  snapshotted lines and the two amounts, and a stored copy could only
  disagree with the lines when staff edit them. A charge on an order
  with no goods (only reachable by staff deleting every line) raises,
  since no rate can follow nothing and a guessed one would be a wrong tax
  document. Every caller already surfaces it: `generate_order_invoice`
  logs and retries, the admin issue/regenerate actions show the
  message, and `submit_document` records a `BUILD` failure.
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

### 3.6 Legal acceptance (`OrderLegalAcceptance`, `LegalDocumentVersion`)

The buyer accepts the store's terms of use and privacy policy by
ordering (`CHECKOUT_CONSENT_SLUGS` in `page_config/legal_documents.py`).
`OrderCreateFromCartSerializer.terms_accepted` (write-only boolean) says
so; `false` is refused with `terms_not_accepted`. With it, the view
resolves the acceptance (`OrderLegalAcceptanceService.accept`) before it
creates anything or looks up a payment, and both creation paths store it
(`OrderLegalAcceptanceService.record`, `order/services.py`) right after
the attribution, inside the creation transaction: one
`OrderLegalAcceptance` row per document.

- **Which text.** `LegalDocumentVersion.objects.in_force(slug, language)`
  (`page_config/models.py`) snapshots the `ContentPage` translation in the
  order's language (`active_language_code`, what `Order.language_code`
  stores), exactly that language and only while the page is `published()`
  (a page scheduled for later is hidden), as the storefront's legal routes
  render it. A version is the title and body keyed by `legal_text_hash`
  (SHA-256 of the JSON pair, so no separator can move between them):
  reused while the text stays the same, a new row once the merchant edits
  the page, and never changed or deleted (`PROTECT` from both the
  acceptance and the page). The admin shows the order's acceptances inline,
  and every version read-only under Content → Accepted legal texts.
- **How.** `source` is `agent_checkout` when the request carries the
  agent gateway's shared secret (`X-Internal-Gateway`,
  `is_internal_caller`), whose UCP/ACP checkouts link both documents for
  the agent to present; any other caller is `checkout_consent`, the
  storefront's ticked checkbox.
- **No text, no order, no payment.** A document with no published,
  non-empty text in the language raises `LegalDocumentUnavailableError`:
  the buyer was shown a broken link, so nothing was accepted. Order
  creation answers 409 `legal_document_unavailable` (`error.document`
  names the slug), logs an error and creates nothing. The cart's
  `create_payment_intent` refuses the same way, because on the
  payment-first path (§3.1) the card is confirmed before the order is
  created: no money moves for an order Django would refuse.
  A store does not get there by an edit: `page_config/legal_guard.py`
  refuses, in the admin and the staff API, unpublishing, scheduling,
  renaming, emptying a served language or deleting a legal page.
- **Every order.** `terms_accepted` is required, and both creation
  paths take the resolved `LegalAcceptance` as a required argument, so
  no order is created without both rows. Orders placed before the field
  existed (`0092_legal_acceptance`, no backfill) have none.

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
  resolved from the signed `parcelId` before verification. The API
  environment (stage or production) is per-tenant too (`box_now_live_mode`, §5.2).
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

**The collected amount must be the order's total.** `handle_payment_succeeded`
(and the `checkout.session.completed` handler) compare what Stripe collected
with `order.calculate_order_total_amount()` (currency, and amount within
`PAYMENT_AMOUNT_TOLERANCE`) before `mark_as_paid`. A charge that does not
match is booked on the ledger (the money moved) but does not pay the order:
`OrderService.flag_payment_mismatch` stamps `AMOUNT_MISMATCH_FLAG` (which
also keeps `auto_cancel_stuck_pending_orders` away from it), notes the order
and mails `OpsAlertKind.PAYMENT_MISMATCH`; the Viva amount/currency refusals
use the same function.

**A poll never writes `COMPLETED` itself.** `GET /order/{id}/payment_status`
(`OrderService.get_payment_status`) does not write the provider's status onto
the row. A polled COMPLETED settles through the same
`handle_payment_succeeded` (and announces through
`announce_stripe_payment_applied`, shared with the webhook), only for a Stripe
intent no gift-card purchase holds; any other provider's payment is settled by
its webhook alone. `PayWayService.check_payment_status` (a second, caller-less
polling writer) was removed.

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

#### 4.2.0 A Stripe refund that FAILS after it was accepted (`refund.failed`, `refund.updated`, `charge.refund.updated`)

Webhook handler: `handle_stripe_refund_failed` (`order/signals/handlers.py`), registered for all three event types, which carry the Refund object; only `status == "failed"` acts (Stripe: a refund is `succeeded` or `pending` when accepted and can move to `failed` later, the money returning to the merchant's Stripe balance, and the merchant must pay the customer another way: docs.stripe.com/refunds).

- The ledger counted the refund when it was accepted, so the handler writes ONE `REFUND_REVERSAL` row (`PaymentLedger.record_refund_reversal`) keyed on the refund id: the three event types and redeliveries book it once. It is capped at what the ledger holds as refunded on that charge, and a refund the ledger never counted (nothing to reverse) is logged and ignored. Matching is by amount, not by row, because a dashboard refund is booked from `charge.refunded` under the event id and carries no refund id.
- `refunded`, `refunded_on`, `balances` and `left_on` subtract a reversal, so the money is refundable again, and `refund_status` derives `payment_status` again (COMPLETED when every refund was reversed, PARTIALLY_REFUNDED when some stand).
- Staff are told twice: a NOTE on the order and the `refund_failed` operational email (§5.4).
- **What is NOT taken back, on purpose.** The refund fired `order_refunded` when it was accepted, and its consequences stand: the gift-card credit and the points released stay (the customer is still owed the money, and taking value back from them would punish a provider failure), the customer email stays (a second email would only confuse; staff are told to pay the customer another way or refund again once the cause is cleared), and the retail credit document stays (it records what the merchant owes). A repeat refund after a failure issues another credit document, which `create_pending_credits` refuses when it would exceed the receipt (a NOTE says so): that case needs the accountant, it is not resolved automatically.
- Stripe's own statement of the charge's `amount_refunded` after a failed refund is not relied on: the reversal is by the failed refund's own amount. The cumulative figure of `charge.refunded` is then compared with the ledger's NET refunded (refunds less reversals): after refund 30 succeeds, refund 20 fails, a `charge.refunded` reporting 30 adds nothing. An event raised BEFORE the failure and delivered after it still counts the failed refund, so `PaymentLedger.reversed_after(charge, event.created)` recognises it by its own timestamp and `handle_stripe_charge_refunded` ignores it (otherwise it would book the reversed 20 again). An event raised between the failure and our recording of it is ignored too, a window of the webhook's own latency; the next event for the charge catches up.

#### 4.2.1 Stripe dispute (`charge.dispute.created`, `charge.dispute.closed`)

Webhook handlers: `handle_stripe_dispute_created` and `handle_stripe_dispute_closed` (`order/signals/handlers.py`). Both find the order by the dispute's `payment_intent` and are guarded by the `webhook_processed_<event id>` metadata flag.

- **Opened** flags the order (`metadata["disputed"]`, `dispute_id`, `dispute_status`), writes a NOTE and emails staff (`send_dispute_notification_email`). The ledger is untouched: until the dispute closes the money is still the merchant's to defend.
- **Closed, `status == "lost"`**: the money is gone. `PaymentLedger.record_chargeback` records ONE CHARGEBACK row against the order's CHARGE (read from the provider by `ensure_charge` when the ledger lacks it), keyed on the dispute id, so a repeat adds nothing. The row lowers `refundable`, so staff cannot refund money already taken back. The order gets a red `CHARGEBACK` history entry ("Chargeback €X"; `ORDER_HISTORY_CHANGE_TYPE_VARIANT`, shown in the order admin's history tab) and staff get the same dispute email with `lost=True`. The row then awaits a staff decision on the goods (`ChargebackDecision`, see "Chargebacks" under the credit documents): whether the sale was reversed decides if the receipt is credited.
- **Closed, any other status** (`won`; `warning_closed`, an inquiry that never became a dispute): clears `metadata["disputed"]` and leaves a NOTE. No row.
- **A refund is refused while a dispute is open.** Stripe: "you can't issue a refund on a disputed charge until your customer's card issuer decides in your favor" (docs.stripe.com/disputes/withdrawing). `OrderService.refund_order` raises `PaymentError` when `PaymentLedger.dispute_open` holds (flagged, no CHARGEBACK row for that dispute id yet). An inquiry (`dispute_status` `warning_*`) is not refused: Stripe lets the merchant refund one to resolve it. An inquiry that escalates to a dispute is not tracked (there is no `charge.dispute.updated` receiver), so Stripe's own refusal is what staff see then. `cancel_order` still cancels and reports the refund as failed.
- **`payment_status` does not move** on a lost dispute, and **`order_refunded` does not fire**: nothing was paid back, so there is no customer email, no Meta `Refund` event and no gift-card release. `refunded` and `refunded_on` count REFUND rows only, so the `charge.refunded` delta arithmetic (§4.2) is unchanged; a partly refunded order that is then charged back stays PARTIALLY_REFUNDED.
- **Loyalty**: `order_charged_back` (sent once per new CHARGEBACK row) reverses the earned points once refunds plus chargebacks cover everything received (`PaymentLedger.fully_returned`, the same decision the refund path takes; §7.3). Unlike a refund or cancellation, it does **not** give back the points the customer redeemed on the order: the customer took the money back through their bank, so what they spent stays spent (owner decision 2026-10-17; `LoyaltyService.reverse_order_points` skips the restore when the order has a CHARGEBACK row). Staff can re-grant by hand. A refund or a cancel takes back ALL the points the order earned even when the customer has spent them since, when `LOYALTY_REFUND_ALLOWS_NEGATIVE_BALANCE` is on (the default): the balance goes negative, later earnings pay it off, and a negative balance blocks redeeming (`redeem_points` needs `balance >= points`). Off, the reversal stops at zero and the spent points survive (earn, spend, refund: the loop the default closes).

#### Dispute lifecycle per provider

| | Stripe | Viva Wallet |
|---|---|---|
| Opened | `charge.dispute.created` | 2054 *Account Transaction Created* whose sale reads `M` / `MA` / `MI` |
| Lost | `charge.dispute.closed`, `status == "lost"` | 2054 whose sale reads `ML`, or a 1797 refund claim (types 13 / 46 / 83 / 90) |
| Won | `charge.dispute.closed`, `won` | 2054 whose sale reads `MW` |
| Ledger key of the CHARGEBACK | the dispute id | the CHARGE's own Viva transaction id (the original sale) |
| Refund while open | refused (documented) | refused (a disputed payment cannot be refunded by the merchant) |

Viva has no dispute webhook, and does not document whether a chargeback arrives as a 1797, so a claim type on the 1797 we already receive is handled and the dispute is otherwise followed through its sale's `StatusId` (developer.viva.com/integration-reference/response-codes/, StatusId table: `M` "cardholder has disputed the transaction", `MA` "Dispute Awaiting Response", `MI` "Dispute in Progress", `ML` "Dispute Lost", `MW` "Dispute Won"; `MS` "Suspected Dispute" is not acted on). The 2054 payload has no `OrderCode` and Viva documents no subtype for the chargeback debit (SubTypeId 2 `FeeChargeBack` is the fee for losing, 162/163 reserve and release funds), so **no SubTypeId is interpreted**: the handler (`_handle_account_transaction_event`) reads `EventData.SaleTransactionId`, finds the store by the ledger (a Viva CHARGE with that id, or an order paid by it: database lookups only, no Viva call for a clearance, fee or payout that is none of ours), authenticates by Retrieve Transaction for that sale, matches the order by the retrieved `orderCode` and acts on the retrieved status. Anything else (no sale id, an unknown sale, another status) is acknowledged and ignored. Each 2054 is deduplicated on `VivaWebhookEvent` (its own `TransactionId` and 2054). The chargeback amount is the retrieved sale's amount (absolute), because Viva documents no disputed amount; the ledger caps it at what the payment still holds (see below). Each store must subscribe the 2054 webhook in its Viva dashboard (Settings > API Access > Webhooks > Create Webhook, one subscription per event type). A 2054 (or a 1797 claim) for a suspended or inactive tenant answers 503, exactly as the payment path does, so Viva redelivers (hourly, 24 attempts): acknowledging it would lose the chargeback for good.

**The chargeback is capped at what the payment still holds.** `PaymentLedger.record_chargeback` records at most captured minus refunds minus earlier chargebacks (`PaymentLedger.left_on`), whichever provider reports it, and logs a warning when it caps (Stripe's amount should fit; Viva's whole-sale amount does not once the sale was partly refunded). A capped entry says so in the red history line ("Chargeback €70.00 (disputed €100.00, €30.00 already returned)"). When nothing is left no row is written, but the history entry ("Chargeback of €X not recorded: nothing is left on the payment") and the staff email still go out, once (`order.disputes.announce_chargeback`; Viva remembers it in `metadata["dispute_resolved_id"]`, because each wallet movement of a dispute is an event of its own).

#### Gift-card purchases are reversed too

A gift-card purchase is not an order, so the order lookups above do not
find it. Each reversal of its payment reaches
`GiftCardService.handle_purchase_reversal`: a Viva refund (once the refunds
reach the purchase amount) or refund claim (types 13 / 46 / 83 / 90, whatever
amount it names), a Viva 2054 whose sale reads `ML`, a Stripe `charge.refunded`
(cumulative; voids once it reaches the purchase amount) and a lost
`charge.dispute.closed`. It voids what is left on every card the purchase
issued whether or not the card was touched, disables it, and records what was
already spent in `GiftCardPurchase.unrecovered_amount` with an ERROR log and a
`gift_card_reversal` ops alert. An OPENED dispute (Viva `M`/`MA`/`MI`, Stripe
`charge.dispute.created`) only alerts: the money is still the merchant's to
defend. The Viva dispute path finds the purchase by `provider_transaction_id`
(the TransactionId the 1796 completed it with; a purchase completed before
`giftcard 0008` has none and is not found).

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

**Settlement is bound to the thing paid for.** A 1796 settles an order only
when the verified transaction carries one of the order's own codes, states its
currency and amount, and both are the order's; a transaction that states
neither (or whose amount is unreadable) is refused and flagged, not settled on
status alone. The gift-card branch (`_gift_card_payment_problem`) makes the
same check against the purchase's own order code, currency and amount.

**Refund webhook (1797, `_handle_reversal_created`).** The event's
`TransactionId` is the REFUND, a transaction of its own; `ParentId` is the
payment it reverses
([Viva](https://developer.viva.com/webhooks-for-payments/transaction-reversal-created/)).
Per Viva's [status and type tables](https://developer.viva.com/integration-reference/response-codes/)
that transaction reads `statusId` `F` (`R`, "fully or partially refunded",
is the status of the ORIGINAL payment), a refund or void
`transactionTypeId` (`VIVA_REFUND_TRANSACTION_TYPE_IDS`; the "Refund
Claimed" types, `VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS`, are chargebacks
and are booked as such, see *Dispute lifecycle per provider*) and a
**negative** `amount`.
The handler used to wait for the refund transaction itself to read
REFUNDED, which Viva never reports, so every real 1797 was skipped — and
recorded as processed, so Viva never retried. Nothing is read from the
unauthenticated body except `ParentId`:

1. `_verify_viva_reversal` retrieves the refund transaction (an error raises
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
Viva's dashboard reached the order. Dispute tracking needs a second subscription, *Account Transaction Created* (2054), in the same place.

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
   `amount_to_be_collected`). Cash has no provider to refund it through, but
   it IS refundable: once collected it counts toward what `refund_order`
   can refund, and the merchant pays it back themselves and records how
   (§7.2, §7.3). Until the courier's payout is reconciled the ledger holds
   nothing for the order and there is nothing to refund.
   A payout row settles an order only when it is that order's own: the
   shipment is found by `POD`; `Customer_RefNo_1/2` (order pk / uuid) are a
   fallback ONLY when the voucher could not be read, never when it is readable
   but unknown (a parcel made by hand in the ACS portal), and a payout that
   remits a different sum than the shipment's COD amount is left unattributed
   for staff review (the "payouts that match no shipment" list) instead of
   being settled at the shipment's amount.
   The status flip and that row are ONE atomic step under the order lock
   (`OrderService.settle_cod_payment`, shared by the ACS reconcile, the
   staff "received outside ACS" action and BoxNow's delivered event): a
   failed ledger insert rolls the status back, so the next payout run still
   sees PENDING instead of skipping an order that is paid but unrecorded.
   The status is read under the lock, so two callers racing for one order
   settle it once, and `order_paid` plus the DELIVERED -> COMPLETED
   auto-advance run only for the call that did.
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
- **Cancelling only the ACS voucher**: the voucher detail page has
  "Cancel ACS voucher" (`AcsShipmentAdmin.cancel_voucher_only` →
  `AcsService.cancel_voucher`, `ACS_Delete_Voucher`). The order is not
  touched; ACS refuses a voucher already on an issued pickup list and the
  refusal is shown. "Issue ACS voucher now" then mints a fresh one
  (`reset_shipment_for_remint`).

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
  provider supporting `kind`, `is_kind_enabled(kind, country_code=...)` (ACS lockers: active stations synced for that country), and an active rate row
  for that country — or `ShippingUnavailableError`.
  `assert_available(...)` adds the weight-cap check
  (`ShippingWeightExceededError`); both are used directly by a
  free-shipping promo, which must not let an unavailable or over-weight
  option through just because it charges nothing.
- **One basis for every "free above X".** `ShippingRate.free_shipping_threshold`
  and `PayWay.free_threshold` are both compared with
  `OrderService.threshold_value(cart_total, discount_total)`: the products
  after promotion and coupon discounts, excluding shipping and the pay-way
  fee (loyalty points and gift cards are payment, not discounts, and do not
  move it). Every caller of `OrderService.shipping_cost` and
  `calculate_payment_method_fee` passes it: both order-creation paths, the
  payment-first verification step and the cart's create-payment-intent
  preview, so the preview and the order cannot disagree. The cart response
  reports it as `freeThresholdValue`, and `/shipping/options` takes it as
  `orderValueAmount`; a client shows "X more for free" from that number,
  never from `totalPrice`. (Before, the fee compared items + shipping and
  free shipping the gross total, so a EUR 39.98 cart with both thresholds at
  EUR 40 paid shipping but not the fee.)
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
- **`/shipping/free-shipping-info`** is the store's standing "free from
  X" promise, for a page with no cart to judge (a product page, a
  visitor who has added nothing). It defaults to
  `ShippingService.initial_country_code()` (the first
  `shippable_country_codes()` entry) when no `country_code` is given,
  echoing whichever it used, and lists `free_threshold_rates(country)`.
- **One distance to free shipping: the cart's `freeShippingProgress`.**
  `OrderService.free_shipping_progress(cart, promotion_result)` decides it
  with the two rules `shipping_cost` charges with, so the cart can never
  promise what checkout will not give:
  - a live free-shipping promotion waives every carrier
    (`promotion_result.free_shipping`); one the cart is short of is a
    `near_miss` teaser the engine has already checked would be granted
    (scope, first order, usage limits, the wholesale gate), and its
    distance is the teaser's, measured on the products before discounts as
    the engine judges a minimum;
  - a carrier's `free_shipping_threshold` zeroes that carrier once
    `threshold_value` reaches it; only
    `ShippingService.free_threshold_rates(country, weight_grams, items)`
    count: the `active_rate` gate, the weight cap and the parcel size,
    so a carrier that cannot take the parcel is no route.

  The rates are judged for the store's first shipping country, the one
  checkout preselects (the cart knows no address; the payload echoes
  `countryCode`); a promotion holds in every country. The cheapest route
  wins, a promotion on a tie; a units teaser ("2 more items") costs what
  the engine's own simulation adds (`PromotionEngine.teaser_cost`).
  `free` once checkout charges 0, else exactly one of `remainingAmount`
  and `remainingQuantity`; `threshold` is the value a meter fills to.
  `null` when the cart has no route at all. `promotionNearMiss` entries
  carry `freeShipping`: those offers are routes the progress has already
  weighed, so a client shows the progress and not them. (Staging webside,
  2026-10-28: a 19,99 € cart showed "add 19,01 € to unlock «free shipping
  from 39 €»" next to "add 30,01 € for free shipping", the carrier's
  50 € threshold read from `/shipping/free-shipping-info`.) Known
  limit: a threshold's distance is the shortfall in `threshold_value`;
  with a percentage promotion running, adding exactly that much in goods
  falls a little short, since the promotion grows with the cart.
- **`ShippingService.offered_rates(country_code)`** is the one definition
  of "the store ships this (provider, kind) to this country": an active
  provider that supports the kind, a registered adapter, the adapter's
  `is_kind_enabled` gate, and an active `ShippingRate`. `available_options`
  (checkout) and `shipping_policy` both iterate it, so they cannot
  disagree.
- **`GET /shipping/policy`** is the store's standing policy for the
  storefront's Organization `hasShippingService` markup: one row per
  `offered_rates` entry over `shippable_country_codes()`, with the rate's
  own `price`, `freeShippingThreshold`, `deliveryDaysMin`/`Max` and
  `maxWeightGrams` (null when unset) and no cart input. Anonymous, cached
  by `@cache_methods` (`ShippingPolicyView`); the `shipping` cache surface
  purges it and declares `ShippingRate`, `ShippingProvider` and
  `shipping_acs.AcsStation` (the locker gate) as `invalidated_by`. A
  carrier's credentials live on the `Tenant` row, which no surface watches,
  so connecting or removing one shows after the TTL or a manual purge of
  the `shipping` surface.
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
  and are the fallback match keys. A row with no readable voucher
  (empty `POD`, no `Customer_RefNo` match) is counted as `skipped` and
  stored all the same, with an empty `voucher_no` and keyed on
  `AcsCodPayout.row_fingerprint` (SHA-256 of the identifying ACS
  columns plus the payment date): the `(voucher_no, cod_payment_date)`
  unique constraint is partial (`voucher_no <> ''`) and a second partial
  unique constraint covers `row_fingerprint <> ''`, so a rerun updates
  the row and two different unreadable rows of one date both survive.
  Rows ACS reported for a voucher we do not know (`unmatched`) and
  unreadable ones alert the store's operators. Staff clear such a row
  from the payout list with "Mark as reconciled" (a dialog asking for a
  note; stamps `reconciled_at` / `reconciled_by` /
  `reconciliation_note`, never edits the payout; service
  `AcsService.reconcile_cod_payout`). Backfill missed dates with
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
- HTTP 403/406 (`AcsAuthError`) is **retryable**; only a persistent
  rejection means a bad key/IP. ACS answers 406 to overlapping calls
  from one account. 2026-10-20 prod saw 22% of tracking calls rejected
  (2% in July), every one inside the `poll_acs_tracking_batch` fan-out:
  each poll makes two calls of 0.6 to 4 s, so six to eight were in
  flight across the worker pods (celery-worker went from 2 to 3
  replicas around 10-19; rejections went from 7-31 a day to 106, then
  224). Staggering task *start* times does not bound that, so
  `AcsClient._call` (every alias) takes one of
  `ACS_MAX_CONCURRENT_REQUESTS` slots per ACS `Company_ID` and store
  (`shipping_acs/concurrency.py`).
  - **Why 2.** ACS publishes no concurrency or rate figure; the number
    is ours. Measured 2026-10-21 over 10 days of production logs
    (10-11 to 10-20, VictoriaLogs): for every tracking task, how many
    ACS tasks were in flight when it started, against whether ACS
    rejected it.

    | In flight | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9+ |
    |---|---|---|---|---|---|---|---|---|---|
    | Rejected | 0.9% | 2.0% | 3.6% | 4.0% | 6.9% | 7.9% | 11% | 17% | 19-50% |

    2 halves the rate against 3 and still serves the batch (below).
    Re-measure the same way before changing it; there is no
    zero-rejection setting, 1 in flight still saw 0.9%.
  - **Reserved slot.** The poll batch is paced to keep its slots busy, so
    without a reserve a shopper's `price_calculation` or
    `address_validation` could wait the full `ACS_SLOT_WAIT_SECONDS`
    behind it and then fall back or 502. The last
    `ACS_INTERACTIVE_RESERVED_SLOTS` (default 1) slots are for interactive
    calls only: the tracking polls (`tracking_summary` /
    `tracking_details`, `background=True` in `AcsClient._call`) may take
    the other `ACS_MAX_CONCURRENT_REQUESTS - reserved` slots, every other
    alias may take any slot and tries the reserve first. One pool with a
    priority split, not a second pool. At 2 slots the polls run one at a
    time, the measured 0.9% level, and one slot stays free for shoppers.
    A setting that leaves the polls no slot refuses to start.
  - **Dispatch spacing.** Mean poll task duration was about 2 s
    (`ACS_POLL_AVERAGE_TASK_SECONDS`), so one poll slot serves one poll
    every 2 s. `ACS_POLL_DISPATCH_SPACING_SECONDS` is derived as
    duration / poll slots (2 s), so the 200-shipment cap finishes about
    400 s after the tick: inside the 15-minute cutoff (a shipment polled
    in the last 15 minutes is not a candidate) and the 13-minute batch
    lock (which is held only while the batch enqueues the polls, so it
    never competes with them). Polls do not queue past
    `ACS_SLOT_WAIT_SECONDS`.
  - **Slots.** redis-py `Lock` keys (`CustomCache.lock`, tenant-keyed),
    non-blocking, with a per-holder token: release deletes the key only
    while the token is still the holder's, so a lease that ran out and
    was taken by another worker is never deleted from under it. The
    lease is `client.worst_case_request_seconds` (every connect attempt
    of `_RETRY_CONFIG` at `ACS_HTTP_TIMEOUT`, urllib3's backoff, then
    one read: 78 s at the 15 s default), derived, not configured. A
    killed worker frees its slot when the lease ends.
  - **Full cap.** A call waits up to `ACS_SLOT_WAIT_SECONDS`, then raises
    `AcsRetryableError` for Celery to retry. With Redis unreachable the
    cap is skipped and logged: a rejected call was never processed, and
    `core/caches.py` sets the policy that an unreachable Redis costs
    speed, not requests.
  - **Retries.** `poll_acs_tracking_one` retries at 10, 20, 40, 80, 160 s
    with no jitter (Celery's jitter draws from zero, which re-entered the
    congestion at once).
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
  task.
- Debugging ACS (call history, pickup-list run records, the shipment
  timeline, LogsQL queries): `docs/acs-debugging.md`.
- The **silent** form of the same refusal is classified as the same
  `blocked_unprinted`. Once a voucher stays unprinted into a later day ACS
  stops naming it and answers `PickupList_No: null`, `Unprinted_Found: 0`,
  `Error_Message: null`, `Unprinted_Vouchers: []` (voucher 9818420312: named
  2026-10-22, silent 10-23; voucher 9819787972, order #448: named 10-27,
  silent 10-28; `ACS_Get_Pickup_Lists` was empty, so reconciliation adopted
  nothing). When the issue returns no `PickupList_No`, reconciliation
  adopts nothing, the day is not all dead vouchers, and EVERY candidate has
  `label_printed_at` NULL, `issue_daily_pickup_list` logs a WARNING ("did
  not name the unprinted voucher(s)") and raises
  `AcsUnprintedVouchersError` with the local unprinted vouchers in
  `raw["Unprinted_Vouchers"]`, so the task takes the one path above: status,
  order notes, no email. If ANY candidate is locally printed the silence is
  unexplained and stays an ERROR log plus `AcsAPIError`; that case failed
  the infrastructure's production smoke gate when it was misclassified. The retired `warn-unprinted-acs-vouchers` beat row is deleted by
  `tenant/migrations/0049`, because `DatabaseScheduler` never removes a
  row whose entry left `CELERY_BEAT_SCHEDULE`.
- The stale digest's "stranded mint" class (`pending_creation` > 24 h)
  excludes orders awaiting their online payment — the auto-cancel owns
  those, and listing them invited an "Issue ACS voucher now" for unpaid
  goods. The exclusion is `awaiting_online_payment_q("order__")`, the
  same rule as `OrderQuerySet.awaiting_online_payment()` and the
  follow-up flag (§2.3): an online order whose payment settled in any way,
  a refund included, is no longer "waiting".
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
- **Environment is per tenant.** `Tenant.box_now_live_mode` says whether
  the tenant's keys are BoxNow production keys (on) or stage keys (off,
  the default). `BoxNowClient` maps it to the platform's
  `BOXNOW_PRODUCTION_*` or `BOXNOW_STAGE_*` API and location hosts
  (settings/env; both sets are always present). BoxNow stage and
  production keys only authenticate against their own API, so a showcase
  store can run on stage keys beside a real store on production. The OAuth
  token cache key carries the environment. After flipping the flag, run
  the locker sync again: stage and production lockers are different sets.
- **A deployment named `staging` can never reach the production API.**
  Staging mirrors the production tenant rows, flag included;
  `BOXNOW_LIVE_MODE_ALLOWED` is off when `SYSTEM_ENV=staging`, and
  `BoxNowClient` raises `BoxNowConfigError` for a live tenant there
  instead of booking a real parcel. Webhook verification reads only the
  tenant's `webhook_secret` and is unaffected. Migration `tenant` 0054
  set the flag from what the platform URL did before: on the production
  deployment every tenant that has BoxNow keys is live, anywhere else
  every tenant is off.
- "Create BoxNow parcel now" (shipment detail action) re-dispatches a
  row left in `pending_creation` by a business error, once its data is
  fixed — the counterpart of ACS's "Issue ACS voucher now". On a
  `canceled` parcel it first archives the dead parcel to
  `metadata["previous_parcels"]` and resets the row
  (`BoxNowService.reset_shipment_for_reissue`), because a cancelled parcel
  keeps its `delivery_request_id` and creation returns an existing
  shipment unchanged; it refuses when the order itself is cancelled.
  BoxNow's `orderNumber` is still the order id, so BoxNow answering the
  new request with `P410` (duplicate) raises the usual creation alert.
- **The compartment size is derived from the products' dimensions**
  (`shipping_boxnow/packing.py`). `Product.length` / `width` / `height`
  are nullable `Distance` fields (stored in metres, edited in cm, mm, m or
  inch next to the weight in the product admin; not in the API, nothing
  outside the server reads them). Limits are `COMPARTMENT_LIMITS_CM`
  (`shipping_boxnow/enum/compartment_size.py`), BOX NOW's published sizes
  (https://boxnow.gr/en/faq): Small H8, Medium H17, Large H36, all W45 x
  L60 cm. The FAQ also lists a Mini (H8 x W24 x L60); the partner API has
  no value for it (manual v7.2, error P406: "one of required sizes 1, 2 or 3
  (Small, Medium or Large)", and the OpenAPI spec 1.68, both under
  `boxnow.gr/media/hidden/`), so it is booked as Small. The
  20 kg weight limit stays on `ShippingRate.max_weight_grams`. The rule is
  conservative, never undersizing: each unit lies on its smallest side
  (height = smallest dimension), its other two sides must fit W45 x L60,
  and units are stacked (no side-by-side packing), so the parcel's height
  is the sum of the units' heights; the answer is the smallest size whose
  height holds the stack. Dimensions compare at millimetre precision
  (metres are floats). `create_shipment_row` stores the result on
  `BoxNowShipment.compartment_size`, which goes out as
  `items[].compartmentSize`.
  **The checkout no longer supplies a size** (the server owns it; an old
  client still sending `boxnowCompartmentSize` is ignored, not rejected).
  **Products without dimensions** get the store's
  `BOXNOW_DEFAULT_COMPARTMENT_SIZE` setting (1 = Small by default, 2 and 3
  allowed, validated). A cart with some measured and some unmeasured items
  takes the larger of the derived size and that setting; with every item
  measured the derived size wins. The free gifts a cart is entitled to
  (`PromotionEngine.gift_pairs`) are added to the cart's lines everywhere the
  parcel is weighed or sized (payment intent, order verification and creation,
  the shipment row, the options flag, the locker search), because they become
  physical order lines. A gift the engine leaves out (sold out) adds nothing;
  one lost to a later stock race only makes the booked size larger.
- **A cart that fits no compartment is refused at checkout**, next to the
  weight cap: `ShippingService.assert_available(items=...)` asks the
  adapter (`ShippingCarrierInterface.assert_parcel_fits`, no limit by
  default) and a `ShippingParcelTooLargeError` becomes the 400
  `invalid_order_data` ("The cart is too large for the selected shipping
  method.") on `shippingProviderCode`, from order creation and from the
  create-payment-intent endpoint. `/shipping/options` finds the cart the
  way every cart endpoint does (`X-Cart-Id`, or the signed-in user's
  cart; `CartService`) and flags each option the cart does not fit with
  `exceeds_parcel_size`, by the same `assert_parcel_fits` rule. The option
  is still listed. With no cart, or an unknown one, it is always false.
  The response varies by cart, so a shared cache in front of this route
  must not serve it across carts.
  `POST /shipping/boxnow/lockers/nearest` (the closest-locker lookup)
  derives its `requiredSize` the same way, from the cart at `X-Cart-Id`
  (the store default when there is none) and refuses a cart that fits no
  compartment with 400 `parcel_too_large`; the old `compartment_size` body
  field is gone. The storefront's locker picker is BoxNow's own iframe
  widget, which this API does not drive.
- **BoxNow is on only when its credentials and warehouse are set**: client
  id, secret, partner id and warehouse id
  (`shipping_boxnow.services.missing_credentials`, one rule for
  `is_configured` and the parcel creation). The warehouse goes out as
  `origin.locationId` and is never replaced by a stand-in; a store that
  left one empty has BoxNow hidden at checkout, and a parcel for an order
  placed before that raises `BoxNowConfigError` naming the field.
  `box_now_notify_phone` is optional (BoxNow's integration manual, §4.4
  "Request a delivery", asks for the sender's name only): it is sent as
  `origin.contactNumber` when set and omitted otherwise.
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

### 5.4 Operational alert emails (`shipping/alerts.py`)

Merchant-operations mail goes to the store's operators
(`tenant_admin_recipients()`; platform `ADMINS` only as the fallback) and
is **per store, per kind**. (The store-staff mails about an order,
`team.new_order` and the two dispute mails, are not ops alerts: they go
through `send_email` to `store_operators()` with no `ADMINS` fallback, §6.2.)
`OpsAlertKind` is the whole list:

| Kind | Sender | Where the work shows when the email is off |
|---|---|---|
| `stale_shipments` | `check_stale_acs_shipments` digest, 09:00 | Dashboard "Stale ACS shipments" (`shipping_acs.attention.stale_shipment_q`) |
| `unremitted_cod` | `alert_unremitted_cod_payouts`, 03:00 | "COD cash ACS has not paid out" (`unremitted_cod_shipments`) |
| `unattributable_cod_payouts` | `AcsService._alert_admins_unmatched_payouts` | "ACS payouts matching no shipment" (`unattributed_payouts`: every payout with no shipment and no reconciliation, no time window) |
| `shipment_rejected` | `alert_admins_shipment_creation_failed` | An `OrderHistory` note on the order, written whether or not the email is on |
| `webhook_failed` | `alert_admins_webhook_processing_failed` | The ERROR log only |
| `payment_after_cancel` | `OrderService.record_payment_after_cancel` | "Canceled orders to refund" (`OrderQuerySet.refund_owed`), until refunded; plus the order note |
| `refund_failed` | `handle_stripe_refund_failed` | A NOTE on the order and a `REFUND_REVERSAL` row on its ledger |
| `review_pending` | `product.tasks.send_review_pending_alert`, queued after commit by `ProductReviewWriteSerializer.save` | Dashboard "Reviews to approve" and the sidebar badge (`pending_reviews`) |

- **One gate.** Every sender calls `send_ops_alert(kind=..., ...)`, which
  returns `False` when the store muted the kind or has no recipient. No
  carrier module imports `send_mail`
  (`tests/unit/shipping/test_ops_alerts.py` enforces it). The stale digest
  used to call `send_mail` itself, so it could not be muted.
- **One setting per kind**, `OPS_ALERT_<KIND>_ENABLED` (`OpsAlertKind.
  setting_name`), a boolean that defaults **on**: a store opts OUT, so a
  kind added later reaches every store. Booleans render as checkboxes in
  the store admin (a JSON list of kinds would be edited as raw JSON, and a
  misspelt kind would silently mute nothing). Adding a kind means an enum
  member **and** an `EXTRA_SETTINGS_DEFAULTS` entry; a test keeps them in
  step. The removed senders (unprinted vouchers, pickup list blocked) are
  not kinds: they no longer email.
- **Muting loses nothing.** A muted digest claims no rows
  (`stale_alert_sent` stays false), so the parcels appear in the first
  digest after the email is switched back on. The money kinds and the
  stale digest are mutable only because the same rule feeds a counter in
  `attention_counts()` (`admin.dashboard.store.queries`); each counter
  opens the list through a filter (`attention=stale|unremitted_cod` on the
  ACS shipment list, `unattributed=yes` on the payout list,
  `refund_owed=yes` on the order list). The payout counter and its
  `unattributed=yes` filter share one `Q` (`unattributed_payout_q`), so
  the list shows exactly what was counted. Payout rows ACS reported with
  no readable voucher are stored too (see the COD reconcile above), so
  they are counted like the rest; a row leaves the count only when staff
  reconcile it.
- **`review_pending`** is one mail per review that ENTERS `NEW`: when a
  customer submits one, and when the owner changes the rate or the
  comment of an approved or rejected one (which sends it back to `NEW`,
  off the public list and the averages). A staff edit, an update that
  changes nothing, and a review that is already `NEW` send nothing. The
  task skips a review moderated before it ran, renders in the store's
  `default_locale`, and links the review's admin page on the store's API
  host. `ProductReview.sync_publication` and
  `ProductReviewQuerySet.moderate` keep `is_published` / `published_at`
  in step with the status on every path that changes it.
- To keep one kind on a store, switch the others off in that store's
  settings; the recipients stay `tenant_admin_recipients()`.

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
| Refund (in-app, staff action or webhook): one per refund | `send_refund_confirmation_email(refund_id)` | `order_refunded` signal (PR #8), queued on commit once per REFUND ledger row | a `RefundNotification` row per refund, inserted to claim the email before it is sent (§7.2, §7.3) |
| Retail receipt registered with myDATA | `send_invoice_email` | from `send_invoice_to_mydata`, once the MARK is persisted (§7.4) | `invoice_email_sent` |
| Retail credit document registered with myDATA | `send_credit_email(credit_id)` | from `send_credit_to_mydata` (§7.4) | `credit_email_sent_<credit id>` in `Order.metadata` |
| Dispute opened (staff) | `send_dispute_notification_email` | `charge.dispute.created` webhook | n/a (rare) |
| Dispute lost (staff) | `send_dispute_notification_email(lost=True)` | `charge.dispute.closed` webhook, once per CHARGEBACK row (§4.2.1) | the ledger row (a repeat records nothing) |

Every customer email is sent through `emails.send.send_email` and carries
the transactional `List-ID` and `List-Unsubscribe: mailto:` headers
(`emails.headers.build_headers`); store-staff mail carries no list headers.

**Each order email is an `EmailType`, declared in `order/emails.py`.**
`order.received`, `order.payment_confirmed`, `order.payment_failed`,
`order.shipped`, `order.delivered`, `order.completed`, `order.canceled`,
`order.returned`, `order.status_refunded`, `order.refunded`,
`order.receipt_issued`, `order.credit_issued`,
`shipping.acs_out_for_delivery`, `shipping.boxnow_at_locker` and the
store-facing `team.new_order`, `team.dispute_opened`, `team.dispute_lost`.
A type holds the template pair, the subject (a msgid with plain named
fields, e.g. `Order #{order_id} Status Update - {status_display}`), the
`build_context(obj, env)` that reads the order for it, a `sample(env)` (an
unsaved object graph the preview renders), the recipient resolver, the
gates and whether the store may switch it off (`delivered`, `completed` and
`shipping.acs_out_for_delivery` can be; the rest are required). The task
calls `send_email(type_key, obj)`; choosing *when* to send (the idempotency
claims above) and the bookkeeping after a send stay in the task. A task
that returned `True`/`False` still does: it maps the `Delivery` explicitly
(`emails.send.was_sent`). A mail the pipeline did not send because the store
switched it off or a gate is closed is a deliberate no-op: the task returns
`True`, keeps its reservation and writes no "email sent" note, except the
store-staff tasks, which return `False` as they did for a store without a
contact address.

`order.status_refunded` is the email the REFUNDED status change sends
through `send_order_status_update_email`; `order.refunded` is the one per
refund (`send_refund_confirmation_email`). They share `order_refunded`. The
status template they and `order_returned` build on lives at
`emails/base/order_status` and is no email of its own: no status reaches
a generic fallback (every status that emails has its own type and
template; PENDING, PROCESSING and SHIPPED return before building one).

The admin email preview (`core/email/preview_service.py`) renders the
type through `emails.render.render`, the function the task's `send_email`
calls: for a real order through `order.emails.preview_object`, which finds
the refund, receipt, dispute or shipment the email is about, so what it
shows is what that order is sent, subject included
(`tests/integration/core/email/test_preview_matches_send.py` sends each
email and compares). An order that has no record the email is about (no
refund, no receipt, no shipment) is reported instead of rendering something
else. Without an order it renders the type's `sample`. A subject lives on
its type and nowhere else: never in `core/email/config.py`.

A receipt or credit mail carries its PDF as an `emails.transport.Attachment`
passed to `send_email`; the bytes are never logged.

Every other domain's emails follow the same pattern (`product/emails.py`,
`giftcard/emails.py`, `b2b/emails.py`, `contact/emails.py`, `user/emails.py`,
`cart/emails.py`; see `docs/emails.md`). A builder states the language of
every translated row it prints (`set_current_language(env.language)`), so a
product, topic or cart line is named in the recipient's language whatever
language the task loaded it in.

The builders read the order's lines (`_lines`) **inside**
`translation.override`: parler fixes a model's language when the instance
is created, so lines a task prefetched before the override named every
product in the worker's language. Do not prefetch `items` in the task.

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
  (`OrderService._seed_language_code` stores
  `core.utils.i18n.active_language_code()` — the request language
  `core/middleware/locale.py: RequestLanguageMiddleware` activates from
  the storefront's `X-Language`), so an order's emails and invoice come
  in the language the customer shopped in.
- Whoever stated no language gets **the store's**, never the platform's:
  `core.utils.i18n.store_language()` is `Tenant.default_locale` of the
  bound store (`LANGUAGE_CODE` only with no store bound).
  `get_user_language` / `get_order_language` fall back to it, and a guest
  product alert remembers the language it was made in
  (`ProductAlert.language_code`).
- Merchant-facing mail (`send_admin_new_order_email`, the dispute,
  low-stock, stale-ACS and B2B-application alerts, the review alert)
  renders, subject included, under `translation.override(store_language())`:
  the worker's active language says nothing about the store. The three
  order-domain ones (`team.new_order`, `team.dispute_opened`,
  `team.dispute_lost`) get their recipients and that language from
  `emails.recipients.store_operators()`: the store's contact and owner
  addresses, one mail each, and **no `settings.ADMINS` fallback**. A store
  with neither address sends nothing (`SKIPPED_NO_RECIPIENT`) and the task
  returns `False`.
- There is no pending-order reminder email. An unpaid online order is
  cancelled by `auto_cancel_stuck_pending_orders` (every 15 minutes), so a
  "complete your order" mail a day later reached an order that was already
  gone; `check_pending_orders`, its template and `PENDING_ORDER_REMINDER_*`
  were removed (`Order.reminder_count` / `last_reminder_sent_at` leave the
  model in `order` 0084 and the columns are dropped by `order` 0090).
- A refused create answers 400 with a stable `error.type`
  (`order/enum/create_error.py: OrderCreateErrorType`); `detail` and
  `cart` are display text in the request's language, never a contract.
  A cart that fails `validate_cart_for_checkout` is `insufficient_stock`
  or `cart_invalid` (`CartNotReadyError`), and a hold that lapsed between
  the cart check and its conversion is `reservation_unavailable`.
- Every order-email builder (`order/emails.py`) runs under
  `translation.override(language)`, and `emails.render.render` renders the
  subject and both bodies under the same override. The language is the
  recipient's, stated by the resolver (`customer_of` uses
  `get_order_language`, `store_operators` the store's language); a task
  passes none.

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

### 6.5 Marketing email and the unsubscribe link

Marketing mail is the mail a customer did not ask for in the moment:
the re-engagement email (`core.tasks.send_inactive_user_notifications`)
and the abandoned-cart reminder
(`order.tasks.send_checkout_abandonment_emails`). Neither belongs to a
subscription topic, so there is no `UserSubscription` row for the
unsubscribe link to end. The customer's refusal is therefore kept on the
**account**: `UserAccount.marketing_opted_out_at` (nullable timestamp,
additive migration; the moment is the proof of when they asked).

- The blanket link (`generate_blanket_unsubscribe_link`, the
  `List-Unsubscribe` URL of both emails) sets it, in the same
  transaction that ends the account's active topic subscriptions
  (`user.views.subscription._apply_unsubscribe`,
  `user.services.subscription.opt_out_of_marketing`). The one-click POST
  (RFC 8058: `List-Unsubscribe=One-Click`, no cookies, always 200) and the
  GET link do the same. A topic link (`/unsubscribe/<token>/<topic>`) ends
  that topic only; a guest subscription's link touches no account.
- **`EmailOptOut` is the one gate.** Every marketing send is a
  `Kind.MARKETING` email type (`emails.declare.marketing_email`), and
  `send_email` skips a recipient with an `EmailOptOut` row for the type or
  for `*` (every marketing type). No task filters on
  `marketing_opted_out_at`; the field is account state only.
  `emails/0002_import_marketing_opt_outs` gave every account that opted out
  before #368 (flag, no row) its `*` row. The pipeline also gives the type
  the RFC 8058 `List-Unsubscribe` headers (Gmail and Yahoo require
  one-click unsubscribe on marketing mail; the HTTPS URI carries a signed
  token, and the relay DKIM-signs both headers).
- A cart reminder skipped (opt-out, or the type OFF) has its reservations
  settled (`abandonment_notified`), so the next run does not find it again
  and a lifted opt-out never sends a months-old reminder.
- Product alerts (restock, price drop) are marketing types too
  (`product.restock_alert`, `product.price_drop_alert`): each fires once for
  one product the customer picked, and they honour the same opt-out. An
  alert skipped because the type is OFF stays armed; one skipped for an
  opt-out is settled.
- Nothing lifts the opt-out by itself, and staff never do: only the
  customer can, through their own preferences
  (`PATCH /api/v1/user/email-preferences`, `set_marketing_preferences`: a
  `*` row is split into rows for the other types, and the flag clears when
  no row is left; `docs/emails.md`). A restock or price-drop alert made by a
  signed-in customer who opted out answers `409 email_opted_out` (a guest's
  is not checked). Staff may record an
  objection a customer made by phone or email (Loyalty & Engagement tab:
  saving the field writes `EmailOptOut("*")`); once set the field is
  read-only.

### 6.6 The store's copy of a contact or feedback submission

`contact.tasks.send_contact_notification_email_task` and
`send_feedback_notification_email_task` send the `team.contact_message`
and `team.feedback` types (`contact/emails.py`) to the store's operators
(`emails.recipients.store_operators`: the contact address, then the
owner's), rendered from `emails/contact/{contact,feedback}_notification.
{html,txt}` in the **store's** language, subject included. The submitter's
address is `Reply-To` (the type's `reply_to`), so replying answers the
customer; it is checked with `validate_email` first, and anything that is
not one bare address (a line break, a display name, two addresses) is
dropped and the mail goes out without `Reply-To`. Files are never attached:
the mail only says they exist. A task returns `False` when nothing was sent
(no operator address, the type switched off, the feedback feature closed).

### 6.7 Account emails (allauth)

`user.adapter.UserAccountAdapter.send_mail` replaces allauth's own
`send_mail`, so allauth's `current_site` is not in the context and must
not be (`SITE_ID` is one store's `Site` row: every store would sign as
it). Every allauth template reads the store from
`build_email_context` instead: `SITE_NAME`, `SITE_URL`, the logo and
theme. `core/templates/account/email/base_message.txt` and
`email_confirmation_message.txt` replace allauth's text layout for that
reason, and every email allauth can send (`account/email/*`,
`mfa/email/*`, `socialaccount/email/*`) has an HTML twin under
`core/templates/`.
`tests/integration/user/test_allauth_email_store_identity.py` discovers
the kinds from the installed allauth, so a template a later allauth adds
fails there until it is covered.

## 7. Cancellation + refund flows

### 7.1 Cancel paths

- **Customer** (the owner, or a guest holding the order's uuid): `POST /api/v1/order/{id}/cancel` → `OrderService.cancel_order(order, reason, refund_payment=True, customer_initiated=True)`. The courier is asked first, see §7.1.1.
- **Store staff**: the same endpoint, with `customer_initiated=False` — the view takes it from `is_store_staff(request.user)`, never from `is_staff`.
- **Admin**: same `cancel_order` (`customer_initiated=False`) via the "Cancel selected orders and restore stock" action. The change form's `status` field is **read-only**: a form save wrote the column straight to the row, bypassing both `cancel_order` (no stock restore — prod orders 242 and 213, 2026-09-08) and the transition table in `update_order_status`. Every other transition is an action too (`mark_as_processing` … `mark_as_returned`, `mark_as_refunded`), routed through `update_order_status`. RETURNED and REFUNDED deliberately leave stock alone (§5.1).
- **Auto**: `auto_cancel_stuck_pending_orders` Celery beat (every 15 minutes) — cancels an unpaid online order once its payment hold has ended (`customer_initiated=False`), see §7.1.0.
  An unpaid online order holds its stock until then, so how many one customer can have waiting is bounded per store: `ORDER_MAX_UNPAID_ONLINE_ORDERS` (default 5, `0` = off; `OrderService._enforce_unpaid_order_limit`, checked in `create_order_from_cart_offline` for online methods only). The customer is the account, or the email (case-insensitive) for a guest; PENDING+PENDING online orders count, offline (COD) and paid ones do not. An order over the limit is refused with a plain message. Counted under the cart lock, so two carts of one customer racing can overshoot by the number in flight.
- **Carrier-driven**: `AcsService._cancel_order_for_dead_shipment` (`customer_initiated=False`; the voucher is already canceled).

`customer_initiated` is a required decision for every caller that is not a customer: it defaults to `True`, so a caller that forgets to choose is held to the courier's answer (§7.1.1) rather than silently skipping it.

`cancel_order`:
0. Customer only: asks the courier to cancel the voucher before anything else (§7.1.1). No lock is held and no transaction is open for that call.
1. Locks order row.
2. Locks every product it touches in pk order (`StockManager.lock_products`, §3.1), then releases stock + reservations (`StockManager.increment_stock`, `release_reservation`).
3. Sets `status=CANCELED` (the save settles an unpaid `payment_status` to `CANCELED` — `Order.settle_unpaid_payment`), records `metadata['cancellation']`.
4. Staff / system only: cascades to the courier voucher via `cancel_attached_shipment` → `ShippingService.cancel_shipment` (PR #2 H). Records the dispatch outcome on metadata. A carrier rejection (e.g., voucher already in pickup list) does not stop the cancel: it is logged, kept in `metadata['cancellation']['shipment_cancel']['error']`, left as a staff-only `OrderHistory` NOTE (`Order canceled, but the courier did not cancel the parcel: …`), and the admin "Cancel selected orders" action raises an error message for it. For a customer cancel the courier already confirmed in step 0, so the outcome is recorded up front and the cascade (and the `order_canceled` signal's safety net) skips.
5. After the cancel has COMMITTED, optional refund via `refund_order` (`OrderService._refund_canceled_order`; `refund_payment=True`, and the order is paid or the ledger still holds refundable money, so an order a provider's dashboard already refunded in part gets the rest back, not nothing). The refund is its own claim, call and record (§7.2): no transaction is open while the provider answers, which a cancel used to hold open across the call. A refund refused before any provider is asked (a dispute is open, nothing left, no credentials) is written to the order's history in plain words and reported as `refund_info = {"refunded": False, ...}`; for a dispute `refund_info["dispute_open"]` is true, the history says the refund waits until the dispute closes, and the admin cancel action says the same.

**Don't bypass**: do not call `order.save(update_fields=['status'])` directly to cancel — you'll skip stock release + email + history. Always go through `OrderService.cancel_order`.

### 7.1.0 The payment hold: stock is released when the payment link expires

An online-payment order reserves its stock at creation, so how long it may wait for its payment is how long anyone can lock stock without paying. The hold is **short, and ends exactly when the payment can no longer succeed** (`order/payment_hold.py`). Releasing earlier would let a payment succeed on an order whose stock is gone (a paid-but-canceled order, §2.2 `record_payment_after_cancel`); releasing later only locks stock.

- **One window, handed to the provider.** `ORDER_ONLINE_PAYMENT_HOLD_MINUTES` (per store, default 30; the validator accepts 30 to 1092) is passed as the expiry of the payment link or session on every path that opens a payment: `OrderViewSet.create_checkout_session` (hosted Stripe Checkout Session and Viva payment order), and `PayWayService.process_payment`, which the `create_payment_intent` and `retry-payment` endpoints use. `confirm_agent_payment` (UCP/ACP delegated payment) charges at once and opens nothing that waits. The payment-first Stripe path (§3.1) creates no order until the money has moved, so it holds nothing.
- **What each provider does with it.**
  - Viva Smart Checkout: `paymentTimeout` on `POST /checkout/v2/orders`, **seconds**, default 1800, `65535` = never expires. After it expires the shopper must return to the store and start checkout again, and Viva sends **no** webhook for the expiry. Before this change the store never sent it, so every Viva link already died after 30 minutes while the order kept its stock for 24 hours.
  - Stripe Checkout Session: `expires_at`, a Unix timestamp **30 minutes to 24 hours** after Stripe receives the request (default 24 hours). A request closer than 30 minutes is refused, so the provider asks for at least 31 minutes; the default 30 minute hold therefore lasts 31 minutes on Stripe. An expired session sends `checkout.session.expired`, and a customer opening it sees an expired message.
  - Stripe PaymentIntent (the order-first `create_payment_intent` / `retry-payment` flow): a PaymentIntent has no expiry, so the sweep cancels it (`StripePaymentProvider.close_payment`, reason `abandoned`) before it releases the stock. An intent Stripe will not cancel (processing, succeeded) leaves the order open for its webhook; the sweep reports `skipped_open`. A retry closes the intent it replaces the same way before it opens the new one (`PayWayService.process_payment`): otherwise the old intent's client secret could still be confirmed after the sweep released the stock. An intent past the point of no return refuses the retry (`previous_payment_in_progress`), and a provider outage refuses it too (`previous_payment_not_closed`).
- **The expiry actually applied is the one stored.** The provider returns `expires_at` (Stripe's own value; for Viva, the reply time plus the timeout, never earlier than Viva's clock) and the caller stores it in `Order.payment_hold_expires_at`, so the two always end together even where the provider moved it. A new link or session replaces it. Until a payment is opened the hold runs from `created_at`.
- **The total is bounded.** `ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES` (default 60, at least the hold) caps the hold from the order's creation: a new link asks for `min(now + hold, created_at + max)` (Viva is told the remaining seconds), so retries cannot chain the stock lock. When no link fits any more the endpoint answers 400 `payment_window_expired` before the provider is called: for every start (`create_payment_intent`, `create_checkout_session`, `retry-payment`, `confirm_agent_payment`) once the limit has passed, and for a hosted session also when less than the provider's `min_hosted_session` is left (Stripe: 31 minutes, because a shorter session cannot be opened and a longer one would outlive the limit). A retry inside the window works as before. The shopper places a new order from their cart: the cart is kept while an order awaits its payment and a cancel leaves it alone. Orders stamped by migration `0083` carry `created_at + 24h`, so a retry on one older than the maximum is refused too.
- **The sweep.** `ORDER_ONLINE_PAYMENT_GRACE_MINUTES` (default 5) is added to the stored instant, and the 15 minute beat adds its own slack, so a confirmation already on its way reaches an open order. Only unpaid (`PENDING` / `PENDING`) orders of an `ONLINE` pay way are swept; COD and carrier-collected orders are not (§4.4). A `PENDING` order whose payment FAILED is swept too, `ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES` (default 30) after the failure: a failed attempt does not end the payment, the shopper may confirm the same PaymentIntent with another card or retry the same Viva payment order. Both buckets ask the provider to close the payment first (`close_payment(order)`): Stripe cancels the PaymentIntent and the order is canceled at once, whatever its hold says. A provider with nothing to cancel (Viva, whose payment order stays payable after a failed transaction until its own expiry; a hosted Stripe Checkout Session, which records no `payment_id` and ends at its `expires_at`) is closed once the stored hold (`payment_hold_expires_at`, plus the grace window) has ended and not before. A provider that cannot be built (its credentials were removed) cannot close anything and takes the same wait. A payment that is not closed (that wait, or a Stripe intent that succeeded or is processing) leaves the order open for its webhook and counts `skipped_open`.
- **A payment that still arrives after the cancel** is not lost: Viva `_handle_payment_created` and Stripe `handle_payment_succeeded` / `checkout.session.completed` route it to `OrderService.record_payment_after_cancel` (§2.2): the money is booked, the order stays CANCELED, ops is alerted and the order appears in "Canceled orders to refund". Stripe re-delivers a failed webhook for up to three days, and a late success is handled the same way.
- **Alert.** After each sweep, when `ORDER_UNPAID_ONLINE_ALERT_THRESHOLD` (default 25, `0` = off) or more online orders are still unpaid, the task logs `orders:unpaid_online_spike` at WARNING with `unpaid_online_orders` and `threshold`. The `GrooveShopUnpaidOnlineOrdersSpike` rule in `grooveshop-infrastructure` (`logs-alerting/vmrule-grooveshop-logs.yaml`) matches that message.
- **Replaces `ORDER_AUTO_CANCEL_PENDING_HOURS`** (the fixed 24 hours). Migration `0083` stamps the orders already waiting with `created_at + 24h` so none is released earlier than it was promised, and deletes the old setting row.

Sources: [Viva payment order](https://developer.viva.com/apis-for-payments/payment-api), [Viva Smart Checkout options](https://developer.viva.com/smart-checkout/smart-checkout-functionality-options), [Stripe: managing limited inventory](https://docs.stripe.com/payments/checkout/managing-limited-inventory), [Stripe: expire a Checkout Session](https://docs.stripe.com/api/checkout/sessions/expire), [Stripe: cancel a PaymentIntent](https://docs.stripe.com/api/payment_intents/cancel).

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

Every path ends in `PaymentLedger.record_refund` (§7.3), so there is one rule for REFUNDED versus PARTIALLY_REFUNDED, and one `order_refunded.send` per REFUND row recorded → `handle_order_refunded` → email + WS notification + Meta Refund event (a share that goes back onto gift cards and points sends `order_stored_value_returned` instead, §7.2 below). The email is one per REFUND row: `handle_order_refunded` queues `send_refund_confirmation_email(refund.pk)` on commit, and the task CLAIMS the row before it sends by inserting a `RefundNotification` (unique per refund, §7.3). A task delivered twice, or a retry racing its original, finds the claim and sends nothing, and one refund's task can never stand in for another's. It replaces a count of ledger refunds compared with a count of emails sent (`metadata["refunds_emailed"]`), which let two workers both send and let one task mark another refund's email as done. The claim is made before the send, so a worker that dies between the two leaves the refund claimed and unsent (the price of never sending twice); a send that fails for good releases the claim.

**Refunds are in the VALUE of what came back** (owner decision, 2026-10-18). `refund_order(amount)` takes the value the customer returned, not a provider amount. `PaymentLedger.refundable_value(order)` is what can still be refunded: the provider's money (`refundable`) plus what the order paid with gift cards and loyalty points that has not gone back (`stored_value_refundable`). **The provider is paid back first; only the excess goes back onto gift cards and points**, in proportion to what each has not yet got back, so both are used up together. A EUR 100 order paid EUR 40 by card and EUR 60 by gift card, a EUR 10 item returned and EUR 10 refunded, puts EUR 10 on the card and NOTHING on the gift card; before this, refunding the card's share released the whole EUR 60 (the HIGH finding of the fraud audit). Refunding everything is `refund_order(order)` (the dialog's default). In code: step 1 of the claim splits `amount` into the provider shares and ONE `RefundAttempt` of kind `STORED_VALUE` (no charge, no provider); the provider shares run first and the stored share only runs when every one of them was made (a declined card refund leaves the cards and points alone); `OrderService._return_stored_value` then, in one transaction under the order lock, credits the cards (`GiftCardService.credit_refund(order, amount, refund_attempt=...)`) and restores the points (`LoyaltyService.restore_spent_points`), settles the attempt, derives `payment_status` and sends `order_stored_value_returned`. What is still owed is read from the gift-card and points ledgers (`PaymentLedger.stored_value` → `StoredValue`: REDEEM less REFUND_CREDIT per card, spent less restored points, the order's loyalty discount in proportion to the points not restored), never from a counter, and under the cards' and the user's row locks, so a cancel and a refund (or two refunds) that race return the value once: the one-credit-per-card-per-order unique constraint (`unique_giftcard_refund_per_order`) is gone, because an order is credited back in parts; the rows name the refund (`GiftCardTransaction.refund_attempt`, `PointsTransaction.refund_attempt`). **`payment_status` is REFUNDED only when the provider's money AND the stored value are back** (`refund_status`), and PARTIALLY_REFUNDED before; an order paid entirely with gift cards and points is refunded the same way (the dialog is offered for it, `refund_open`). Rounding: the stored share is split to the cent (cards) and the point (points), rounded down, so the LAST refund must ask for what is left, the dialog's default; asking for the rounded figure can leave a cent. A cancel still returns the whole redemption (`order_canceled`), and a chargeback leaves the stored value where it is (owner decision). No refund email is sent for a share that moved no money (the email is per REFUND row); the order's history notes what went back and a REFUNDED order gets its settlement credit document as usual. **Confirming a bank transfer (`OrderService.confirm_offline_payment`, admin action "Confirm payment received").** `payment_status` and `payment_id` are read-only in the order form (a form save once marked orders paid with no charge behind them), so money settled off the platform needs its own door. It is offered only for a `PaySettlement.OFFLINE_TRANSFER` order still PENDING, for exactly `amount_due` and with the bank's reference (required; the ledger key `(bank_transfer, reference, OFFLINE)`, so one transfer cannot pay two orders). Under the order lock it writes an `OFFLINE` ledger row (`PaymentTransactionKind.OFFLINE_COLLECTION`, description "Confirmed by <email>: <note>"), `mark_as_paid`, an `OrderHistory` PAYMENT entry (previous status, reference, amount, `confirmed_by`, when = the row's time) and an admin log entry, and moves a PENDING order to PROCESSING (which fires `order_paid`). Online providers, courier cash and carrier-terminal orders are refused here: their payments are recorded only by the provider's or courier's own confirmed event (the #251 guards stand). The `OFFLINE` row counts as received money like a CHARGE and, like courier cash, has no provider to refund it through: a refund of it is paid back by hand and recorded with a repayment reference (`is_cash_collection`).

**The staff action** (`OrderAdmin.refund_payment`, a dialog on the order's detail page) is offered whenever money paid for the order can still be refunded (`PaymentLedger.refund_open`: the ledger holds refundable money, or the order is paid through a registered provider whose capture the ledger never recorded, which `refund_order` reads from the provider once; decided without a provider call, so drawing the button costs nothing), whatever the order's status: a return (DELIVERED, COMPLETED, RETURNED), a goodwill refund, or a CANCELED order whose automatic refund was declined or that was paid after the cancel (the dashboard's "Canceled orders to refund" list is exactly what this action serves). It was limited to `RETURN_HANDLING_STATUSES`, which left those canceled orders refundable only in the provider's dashboard. Staff need the order's change permission. It asks for an amount (defaulting to what the ledger says is still refundable) and a reason, calls `refund_order`, and reports the service's own refusal (nothing left, more than the remainder, not paid through a provider, cash on delivery not collected yet) or its failure to the staff member. Where the order holds cash a courier collected (a `COD_COLLECTION` row with money left) the dialog also shows **"How it was paid back"** (`repayment_reference`: the bank transfer id, say), and the form requires it exactly when the amount asked reaches that cash (`PaymentLedger.plan_refund` on the typed amount), so a refund inside a card payment of a mixed order is not asked for one. It is the same action and the same service call as a provider refund; see "Refunding cash on delivery" below. A refund split across payments can be declined part-way, so the message distinguishes three outcomes: all of it refunded, `€50 of €60 refunded; the rest was declined: <reason>`, and "was not refunded; see the order history". It writes an admin log entry whenever money moved, the partial case included (who refunded what and why: the ledger row keeps the reason, and a staff identity cannot be a foreign key into a tenant schema). It changes neither the order status nor the stock: moving the order to RETURNED / REFUNDED stays the "Mark as ..." actions, and restocking returned units is `POST order-items/<id>/restock` (below). The read-only ledger is the order page's "Payment Transactions" tab.

**`POST /api/v1/order-items/<id>/restock`** (staff; it was `.../refund`) puts returned units of one line back in stock and records them on the line (`refunded_quantity`, `is_refunded`). It moves NO money and says so: what the customer is paid back is decided at order level, because a line's snapshot price is not what was paid for it (tax, shared discounts, shipping). It used to answer "Refund processed successfully" with a `refunded_amount` for money that never moved.

**There is one restock path: `OrderItem.refund(quantity, performed_by=, reason=)`.** The endpoint and the order page's **"Restock returned items"** action (`OrderAdmin.restock_returned`, a dialog with one quantity per line that still has units not put back, and a reason) both call it, so the guards live in the method and hold for both. Under the order's row lock, then the line's (always in that order, so the two paths cannot deadlock): the order must be DELIVERED, COMPLETED or RETURNED; the quantity must be positive and no more than `quantity - refunded_quantity`, so a unit is never put back twice and never more than were sold (the DB check constraint is the backstop); the stock goes through `StockManager.increment_stock`, which locks the product row (a variant is its own product, so each line restocks its own) and writes a `StockLog` row with the staff member (`performed_by`), the time and the reason. The admin action wraps all lines in one transaction (all-or-nothing), writes an admin log entry, needs the change permission on order lines, and is offered only while a line has units left on an order in those statuses. Nothing here touches payments; after a refund the admin points staff at the restock action but never does it unasked, because returned goods may be damaged.

**Refunding cash on delivery.** A courier's collection is a `COD_COLLECTION` row (§4.4), and the ledger counts it as money received: `captured`, `balances`, `refundable` and `plan_refund` read CHARGE and COD_COLLECTION rows alike, so `refund_status` (refunded ≥ received) works for a COD order as for a card one. Before this, a refund of a collected COD order was refused (`This order was not paid through a payment provider...`): `captured` and `balances` read CHARGE rows only, so `refundable` was 0 and no REFUND row, no status change and no myDATA credit note could follow the bank transfer the merchant had already made. There is still no provider to call for cash, so `refund_order(..., repayment_reference=...)` records the share the merchant paid back themselves:

- A plan share against a `COD_COLLECTION` is written with `PaymentLedger.record_cod_refund`: a REFUND row whose `charge` is the collection, `provider="manual"` (`MANUAL_REFUND_PROVIDER`, who moved the money) and `provider_reference = f"order-{order.pk}-{collection.pk}-{reference}"`. Nothing is sent anywhere; the staff member's word is the evidence.
- `repayment_reference` is **required exactly when the plan touches such cash**, and is validated before the first provider call (a mixed order is never refunded half): blank is refused (`Say how the cash was paid back...`), so is one longer than `REPAYMENT_REFERENCE_MAX_LENGTH` (100). The admin form checks the same rule up front and removes the field when the order has no cash left to refund. A reference given for a refund that touches only provider payments is ignored.
- **Idempotent**: the reference is scoped to the order and the collection in the ledger key, and the unique constraint on `(provider, provider_reference, kind)` is what keeps it from being booked twice. The service refuses a reference already used on that collection (`A refund with this reference is already recorded for this order.`) before writing anything, so a double submit books one refund and the second answer is that message. The same transfer id on another order is its own refund. Two parts of one COD order need two references.
- One REFUND row, one `order_refunded` per share, so the 11.4 credit note (`create_pending_credits` reads every REFUND row of the order, whoever moved it), the refund email, loyalty and Meta CAPI run exactly as for a Stripe or Viva refund. `payment_status` is derived from the ledger by `record_refund`.
- Under the same `select_for_update` lock as every refund, never more than `PaymentLedger.refundable`.
- An order whose cash was not collected yet holds no ledger row, so `refundable` is 0 and the error says `The payment on delivery has not been collected yet, so there is nothing to refund.` (a COD or carrier-terminal pay way with a payment status that is not paid). The cancel path (`cancel_order`) passes no reference and only runs for an order with a `payment_id`, which a COD order does not have; a mixed order canceled that way would be refused whole, before any provider call, and report `refund_info["refunded"] = False` (staff then use the Refund action).

**What `refund_order` refunds comes from the ledger, not from the order.** `Order.amount_due` is the amount owed when the order was created and is never consulted. Under the order row lock it reads what is left (captured minus refunded, payment by payment) and:

- refuses an amount that is not positive, and an amount above the remainder (`Refund amount (X) cannot exceed the refundable amount (Y)`), before any provider is called; a repeat refund that finds nothing left raises `This order has already been refunded.`; an order the ledger holds no provider payment for raises `This order was not paid through a payment provider...` (cash on delivery, a gift card, a hand edit);
- plans the refund payment by payment, oldest first: an order paid twice holds two CHARGE rows and a refund larger than one is split across them, one provider call each, one REFUND row each (a COD_COLLECTION share is recorded with no provider call, see above);
- works in **three steps, so no transaction is open while a provider answers**. Every connection kills a session idle in a transaction after ten seconds (`idle_in_transaction_session_timeout`, `settings.py`), and `refund_order` used to hold its transaction and the order lock across the provider's HTTP call: a slow provider meant a refund made there and lost here (and, inside `cancel_order`, a whole cancel rolled back). Now: **(1) claim**: under the order lock, validate against the ledger and write one `RefundAttempt` per share (`order/models/refund_attempt.py`: order, `kind` `PROVIDER` or `STORED_VALUE`, the charge it refunds (none for stored value), amount, reason, a UUID `idempotency_key`, `PENDING` / `SETTLED` / `DECLINED`), then commit. While an attempt is live (`PENDING` and younger than `ATTEMPT_RESERVATION`, one hour) its amount is held back from its charge in `PaymentLedger.balances`, so a second refund cannot take the same money. **(2) call** the provider with `idempotency_key=f"refund-{attempt.idempotency_key}"` and no transaction open. **(3) record**: under the lock again, `record_refund` writes the REFUND row, settles the attempt and signals `order_refunded`;
- records each refund the provider accepted as it goes, and signals it, so a failure on the second payment keeps (and has announced) the first; the shares after a refused one are released (`DECLINED`), they were never called;
- returns a `RefundResult` (`order/services.py`): `refunded` (the money that actually moved, a `Money`), `refund_ids`, and `declined` (the provider's answer for the share it did not make, `None` when every share was). Never a yes/no: a refund declined part-way reports what DID move, so no caller tells staff "nothing was refunded" about money that left (`complete` and `paid_something` are the two questions callers ask). `declined["error"]` can carry raw provider text, so it is for logs and staff screens, never a customer;
- for an order whose capture the ledger never recorded (it settled while the previous release was serving, or was created from an already-succeeded intent whose event never reached it), reads the capture from the provider once and records it (`PaymentLedger.ensure_charge`); a capture the provider cannot confirm refuses the refund (staff NOTE, nothing sent) instead of leaving money returned and the order unbooked.

**Attempts, keys and what a retry means** (Stripe: docs.stripe.com/api/idempotent_requests). The attempt's key is what the provider receives. Stripe keeps the first answer for a key for at least 24 hours and answers a repeat of the same request with it instead of refunding twice; `StripePaymentProvider.idempotent_refunds` says so. A request that matches a live pending attempt (the same amount, or a refund of everything once the attempts hold everything that was left: a double click, a retry after a crash or a timeout) reuses that attempt and its key, so Stripe hands back the refund it made and the retry records it once. Viva's cancel call takes no key (`idempotent_refunds` is False), so a provider like it is NEVER asked twice for one refund: an answer that was lost cannot be told from a refund never made, and the request is refused with "its result is not known yet"; Viva's 1797 webhook books the refund and settles the attempt. Cash shares are not attempts to resume: their `repayment_reference` is their key. A different amount is its own refund under its own key. **What each outcome does to the attempt**: a refusal (`success` false without `outcome_unknown`: Stripe 4xx, `Success: false` from Viva) marks it `DECLINED` and frees its amount; an **unknown outcome** (`outcome_unknown` in the answer: a timeout, a dropped connection, a 5xx, or Viva accepting without a `TransactionId`, which cannot be booked under an id the 1797 would not repeat) keeps it `PENDING`, because the refund may have been made, writes a staff note saying so, and lets the webhook or a retry settle it. An attempt never settled stops holding its amount after an hour. **A webhook that books the refund mid-call** (`charge.refunded` or 1797 arriving before step 3) settles the matching live attempt (same charge, same amount) in `record_refund`, and step 3 then books nothing and signals nothing: the refund is counted once and announced once. A Stripe attempt Stripe ANSWERED with an error keeps that answer for its key for up to 24 hours: refund it in the dashboard instead, and the webhook records it.

**Stripe refund statuses** (docs.stripe.com/api/refunds/object): `succeeded` and `pending` are refunds Stripe took on (a part refund reports `succeeded` like a full one, so the status never says whether the order is fully refunded; the ledger's sums do), and are recorded, in REFUNDED or PARTIALLY_REFUNDED, both settled statuses a late `payment_intent.succeeded` cannot write over. `requires_action` waits on the customer and `failed` / `canceled` returned nothing: none is recorded, the order stays COMPLETED, and staff find Stripe's refund id and status in a NOTE on the order. The provider no longer reports a payment status at all (`refund_payment` returns `refund_id`, `amount`, `payment_id`). A refund Stripe accepted and later fails (`refund.failed`) is not reversed on the ledger: it has no kind for that, and nothing handles the event yet.

**The `order_refunded` contract** (`order/signals/__init__.py`). It carries `amount`, a `Money`: what THIS refund paid back, and `refund`, the REFUND `PaymentTransaction` row it recorded (the confirmation email is queued for it), both never absent (receivers that use them declare them as required arguments, so a sender that leaves one out raises). `order.payment_status` is already written (by `record_refund`) and says how much of the order is back: PARTIALLY_REFUNDED while part of the payment remains refundable, REFUNDED once nothing does. **Whatever hangs off the order's total is released only on REFUNDED, once, with the last refund**: the gift-card redemption (`giftcard.signals.handle_order_refunded_giftcard` → `GiftCardService.credit_refund`, the whole redemption) and the loyalty points (`loyalty.signals.handle_order_refunded_loyalty`, which is all-or-nothing and once per order). A partial refund is paid back by the provider first, so a slice of the redemption on top would hand back more than that refund was worth (a EUR 5 refund once put EUR 60 back on a gift card), and the one-credit-per-card ledger could not top the card up when the rest is refunded. The email, the toast, the history row and the Meta `Refund` event fire on every refund, partial Stripe dashboard refunds included (they used to be silent).

**A refund the provider did not make is not recorded.** `refund_order` returns a `RefundResult` with `declined` set and changes nothing for that share: `payment_status` stays COMPLETED (or whatever the shares already made left it), no REFUND row is written for it, `order_refunded` does not fire for it. It leaves a staff-only `OrderHistory` NOTE (`Refund via <provider> was not processed: …`; `NOTE` is not a customer-visible history type). `cancel_order` still cancels and reports `refund_info = {"refunded": False, …}` with no provider text, whose `message` says "only part of the payment was refunded" when a split refund was declined part-way; the admin "Cancel selected orders" action raises an error message for it.

**Viva (`VivaWalletPaymentProvider.refund_payment`).** Cancel Transaction requires `amount` (cents), so the request always carries it, and so does every call now: the service always names an amount (a full refund is the ledger's remainder, never "ask Viva what it captured"). HTTP 2xx is not success: Viva reports refusals in a 200 body, so only `Success` true with `ErrorCode` 0 counts, and the body's `TransactionId` (the refund transaction) is the ledger's reference for the refund. `DELETE /api/transactions/{id}` has no idempotency parameter (Viva's `idempotencyKey` is on the `/acquiring/v1/transactions/{id}` route), so none is sent; Viva refuses a refund above what is left on the payment, and the ledger's unique constraint on the refund transaction id keeps it from being recorded twice. Before #134 the amount was omitted and the body ignored, so every in-app Viva refund booked REFUNDED while Viva kept the payment at `F`.

### 7.3 The payment ledger (`PaymentTransaction`)

`order/models/payment_transaction.py`, written only through `order/payment_ledger.py` (`PaymentLedger`). Append-only, like `GiftCardTransaction`: a row is never updated or deleted, and `Order` → ledger is `PROTECT` (deleting an order for real has to remove its ledger first, as the demo reset does).

| Column | Meaning |
|---|---|
| `order` | the order (`payment_transactions`) |
| `kind` | `CHARGE` (a provider captured money), `REFUND` (a provider paid it back, or the merchant paid cash back themselves), `CHARGEBACK` (a lost dispute took the money back: written when it closes against the merchant, never when it opens; it is not a refund and moves no `payment_status`), `COD_COLLECTION` (a courier collected cash), `REFUND_REVERSAL` (a refund the ledger counted that the provider later reported FAILED: the money counts as held again, §4.2; it names the charge like a refund and is keyed on the provider's refund id). |
| `amount` | always positive; the kind gives the direction |
| `provider` | `stripe`, `viva_wallet`, or the courier's `acs_cod` / `boxnow_cod`; `manual` on a REFUND the merchant made themselves (cash on delivery paid back by bank transfer or in cash) |
| `provider_reference` | the provider's own id: PaymentIntent, Viva transaction, refund id, voucher / parcel (a Stripe dashboard refund has no id in `charge.refunded`, so it carries the event id) |
| `charge` | on a REFUND or a CHARGEBACK, the CHARGE it reverses (an order paid twice is refunded payment by payment); null otherwise (a DB check enforces both) |
| `description` | the reason given for a refund |
| `created_at` | when it was recorded |

`UNIQUE (provider, provider_reference, kind)` is the idempotency of every writer: a delivery repeated by the provider records nothing the second time (`record_*` return `created=False`). Writers (all under the order row lock): `OrderService.handle_payment_succeeded`, `handle_stripe_checkout_completed`, `_handle_payment_created` (CHARGE); `OrderService.settle_cod_payment`, reached from `AcsService._mark_cod_order_paid_if_pending` and `BoxNowService._mark_cod_order_paid_on_delivery` (COD_COLLECTION, written in the same atomic step as the payment status, §4.4); `OrderService.refund_order`, `handle_stripe_charge_refunded`, `_handle_reversal_created` (REFUND, through `record_refund`); `handle_stripe_dispute_closed` (CHARGEBACK, through `record_chargeback`). The Viva handlers (`_handle_account_transaction_event`, and `_handle_reversal_created` for a refund-claim type) book it through the same `record_chargeback`.

**`RefundNotification`** (`order/models/refund_notification.py`) is the claim on a refund's confirmation email: one row per REFUND row (a one-to-one, so the database arbitrates between two tasks), inserted before the email is sent and deleted when the send fails for good. It is a side table, not a column on the ledger, so the ledger's rows stay the money facts they are and are never updated; who has been told about a refund is delivery bookkeeping with a lifecycle of its own. It follows its ledger row on delete (`CASCADE`).

Everything else is read from the sums: `PaymentLedger.captured` (money received: CHARGE and COD_COLLECTION), `refunded` (REFUND, by a provider or by the merchant, less REFUND_REVERSAL), `charged_back` (CHARGEBACK), `fully_returned` (refunded plus charged back covers captured, what the loyalty reversal asks), `refundable` (per payment, collected cash included, less what a chargeback took and what live refund attempts hold), and **`refund_status(order)`, the one function that decides REFUNDED versus PARTIALLY_REFUNDED** (refunded plus charged back ≥ captured; chargebacks count, so a chargeback before the last refund no longer leaves the order PARTIALLY_REFUNDED with nothing refundable and the gift card unreleased; and COMPLETED again when every refund was reversed). `record_refund` writes the derived status onto the order in the same step as the row, so no path decides it for itself and it cannot go backwards. `payment_status` stays a column (every filter, serializer and guard reads it); the ledger is what it is derived from after a refund.

**What the customer owes is not what was collected.** `Order.amount_due` (`amountDue` in the API) is what the order costs the customer: `calculate_order_total_amount()` (lines and extras, less promotion, loyalty and gift card), written at creation and again where a deduction changes (the standalone loyalty redemption). It says nothing about money collected, which is the ledger's CHARGE and COD_COLLECTION rows.

What is still to be paid is **`Order.remaining_amount`** (`pricingBreakdown.remainingAmount`): `amount_due` while the payment is not settled, and 0 once `payment_status` is in `SETTLED_PAYMENT_STATUSES`. It is read from the status and never from the ledger, because a settled order can hold no ledger row: a payment staff recorded by hand, cash collected under a `payment_method` the 0067 backfill does not key on, and the demo tenant's seeded orders would all read as unpaid on a ledger sum. So a PENDING / PROCESSING / FAILED online order owes its whole `amount_due` (the storefront's "pay €X" state), a COD order owes it until the courier collects (it is also the amount the courier is told to collect: `AcsShipment.cod_amount`, `BoxNowShipment.amount_to_be_collected`), and a COMPLETED order owes 0. A refund never creates an amount owed (PARTIALLY_REFUNDED and REFUNDED stay at 0), and a CANCELED order owes 0 (it closed unpaid, so no payment will be collected: `PAYMENT_CLOSING_STATUSES`).

Whether an order is PAID is `payment_status` alone (`Order.is_paid`, `isPaid`, and the `is_paid` order filter: `true` is COMPLETED, `false` is every other status). The amount due never decides it: an unpaid order owes money too, and a paid one can owe 0.00 when deductions covered the whole total.

**`Order.metadata["refunds"]` is gone**, and with it the Stripe `charge.refunded` `webhook_processed_*` flag and `PaymentStatus.after_refund`. `PaymentProvider.captured_amount` is gone too: providers refund an amount they are given.

**Migration `0067_backfill_payment_transactions`** wrote rows for the orders paid before the ledger existed, from what the rows prove and nothing it would guess: CHARGE (amount `paid_amount`, the column `Order.amount_due` was called until `0070`, which checkout verified against the provider) for Stripe and Viva orders, COD_COLLECTION for `acs_cod` / `boxnow_cod` orders, REFUND for `metadata["refunds"]` entries that name a provider refund id and an amount (the old `"full"` marker means whatever the payment still held; a Stripe `charge.refunded` entry's cumulative total becomes one row for the part the named refunds do not cover). It could not reconstruct: the amount the provider actually captured (it uses `paid_amount`); any payment but the last of an order paid twice under two Viva codes (`payment_id` holds only the latest); a COD collection's courier reference (the row is keyed `order-<id>`); who refunded (a staff identity cannot be a foreign key into a tenant schema); a refund no provider id was kept for (the four production Viva orders refunded "in-app" before #134 show REFUNDED but the money never left Viva: they get their CHARGE and no REFUND, so the ledger shows the payment refundable, which is true); and anything pods of the previous release wrote while it ran (a payment recorded in that window is read from the provider when a refund first needs it). `payment_status` was left alone.

**Migrations `0070_order_amount_due` and `0071_order_drop_paid_amount_columns`** rename `Order.paid_amount` to `amount_due` over two releases (`docs/migrations.md`). `0070`, the expand step, adds `amount_due` NOT NULL with a `db_default` and its currency column, copies `paid_amount` into them, and takes `paid_amount` and `paid_amount_currency` out of Django's state only; both stay in the database, nullable, so the new code's INSERTs (which no longer name them) work and the release still serving can keep writing them. django-money builds the currency column itself and cannot give it a `db_default`, so it is added with its own `ALTER TABLE ... ADD COLUMN ... DEFAULT 'EUR'` and declared in state separately. `0071`, the contract step, ships once the storefront and the gateway read `amountDue`. It declares `contract_of = [("order", "0070_order_amount_due")]`, re-copies `paid_amount` into `amount_due` where `amount_due = 0 AND paid_amount > 0` (the orders the previous release created while it still served, which left `amount_due` at its default) and drops both columns; its reverse re-adds them nullable, without values. **The API expanded the same way**: the `0070` release emitted `paidAmount` (top level and in `pricingBreakdown`) beside `amountDue`, both `deprecated` in the schema, because the storefront's and the gateway's generated schemas required `paidAmount` and removing it in the release that added `amountDue` would have failed every order response parsed between the two rollouts. The `0071` release removed it with the columns (`OrderSerializer.paid_amount`, the `paid_amount` key of `get_pricing_breakdown` and its schema), so `paidAmount` is no longer anywhere in the API; the `paidAmount*` filters and ordering went with `0070`.

### 7.4 Retail tax documents (myDATA): a receipt at shipment, a credit per refund

`order/mydata/` (builder, client, parser, service), `order/invoicing.py`, `order/credit_documents.py`, `order/models/invoice.py` and `retail_credit.py`. The platform issues **retail** documents only, over the ERP `SendInvoices` channel, with the tenant's own credentials (`tenant.credentials.mydata_credentials()`):

- **11.1 retail receipt** (ΕΛΠ art. 12(1)): the order's `Invoice` row. The model keeps its name from when it also carried B2B invoices; it is the receipt now.
- **11.4 retail credit document** (art. 12(4)): a `RetailCredit` row. AADE forbids `correlatedInvoices` on 11.4 (error 205, confirmed against the dev API on 2026-10-12), so the payload names no receipt and `specialInvoiceCategory` 13 ("Δυσχέρεια Συσχέτισης", optional and valid only for 11.4/14.30) is not sent; the receipt-to-credit link exists only in `RetailCredit.invoice`, the admin and the credit PDF ("Credits receipt X (MARK)").

Both are classified `E3_561_003` + `category1_1` ("Έσοδα από Πώληση Εμπορευμάτων"; `category1_3` is services), a pair AADE's combinations sheets for 11.1 and 11.4 allow.

**Who may issue over this channel.** Retail receipts are issued through a fiscal device (ΦΗΜ) or a licensed e-invoicing provider (ν.4308/2014 art. 12 §8, §10); AADE: «δεν μπορεί το ERP να αντικαταστήσει την υποχρέωση χρήσης ΦΗΜ. Μόνο η επιλογή πιστοποιημένου Παρόχου» (myDATA FAQ 23/5/2022, Q37). The ERP channel is only for a business AADE exempts from the ΦΗΜ (ΠΟΛ.1002/2014; online sales are not exempt as such), and since ν.5222/2025 art. 238 (28/7/2025) even exempt businesses are pointed to timologio or a provider. So the default — and every store that takes real orders today — is that the store issues its receipts and credits outside the platform, and the platform issues nothing. A licensed-provider integration is a parked proposal (issue #197).

**The AADE sandbox is the exception.** Documents sent to `MYDATA_ENVIRONMENT=dev` (`mydataapidev.aade.gr`, "για τη φάση της ανάπτυξης και διενέργειας δοκιμών" in the myDATA REST API documentation) are test registrations, not tax documents. A store that takes no real orders may issue there without a recorded ground: a demo store (`Tenant.is_demo`), or any store outside the production deployment (`settings.REAL_ORDERS_DEPLOYMENT` is false only for `SYSTEM_ENV` `staging`, `dev` and `ci`, so an unknown deployment name fails closed). `MyDataConfig.issues_to_sandbox_only` is that condition. A real store in production needs the ground in the sandbox too: the setting defaults to `dev`, so exempting the sandbox everywhere would let any store that switches myDATA on email sandbox "receipts" to its customers.

**The demo store's specimen receipt.** A demo store (`Tenant.is_demo`, read through `bound_tenant()`) cannot show a customer a real receipt: it holds no ΦΗΜ ground, so a platform receipt exists only when it issues to the AADE sandbox, and a sandbox "receipt" is a test registration. Instead `GET /order/{id}/specimen/download` renders, on demand, the same `invoices/invoice.html` through the same `_build_context` and `_render_pdf_bytes` as the real receipt, from an unsaved `Invoice` and the live order, with a «ΔΕΙΓΜΑ» watermark (a `position: fixed` box, which WeasyPrint repeats on every page), the title line "SPECIMEN - not a tax document", and no QR code (nothing is registered, so nothing to verify). `order.invoicing.specimen_available` is the one gate, used by the endpoint and by `OrderDetail.hasSpecimenReceipt`: a demo store AND `receipt_issuable(order)` (a RECEIPT order at SHIPPED onwards, `RECEIPT_ISSUING_STATUSES`); any other store and any earlier status answer 404 and `false`. It never issues: no `Invoice` row, no `InvoiceCounter` slot, no stored file, no myDATA call, nothing emailed; the PDF is streamed and forgotten. The API only exposes the flag and the download; the storefront button, when it lands, is to sit beside (never instead of) the real receipt's `hasInvoice`. Access is the same as the receipt's: the owner, staff, or a guest holding the order's `?uuid=` (`/invoice`, `/invoice/download` and the specimen route are all in `GUEST_UUID_QUERY_ACTIONS`; a uuid never opens an account-owned order, and the metadata's `downloadUrl` carries the uuid for a guest order). Each call is a WeasyPrint render on the request worker, so the route carries its own fail-closed throttle (`OrderSpecimenThrottle`, scope `order_specimen`, 10/minute per user or IP; the shared demo logins pool into one bucket). Pinned by `tests/integration/order/test_specimen_receipt.py`.

**Settings.** `MYDATA_ENABLED` is the master switch, and `MYDATA_FHM_EXEMPTION_GROUND` records the legal ground of the store's ΦΗΜ exemption (the ΠΟΛ.1002/2014 provision, the accountant who confirmed it, the date). `MyDataConfig.is_ready()` needs both (the ground unless `issues_to_sandbox_only`), a known `MYDATA_ENVIRONMENT` (anything but `dev` or `prod` is refused, never guessed), plus the tenant's credentials; until then nothing is built or sent, whoever asks: `generate_invoice` refuses to issue (`ReceiptNotIssuable`), the order's "Invoicing & myDATA" actions are hidden and refused (`has_retail_documents_permission`), the order's Receipt card says the store issues the receipt outside the platform, and the dashboard shows a danger alert when the switch is on but the ground is blank (outside the sandbox exception), or when the environment is invalid. "Regenerate receipt" only re-renders a receipt that exists; it never creates one. The customer sees a receipt (`hasInvoice`, `/order/{id}/invoice`, `/invoice/download`; a guest with the order's `?uuid=` too) only once AADE registered it (`Invoice.is_deliverable`: CONFIRMED and a stored PDF). `MYDATA_AUTO_SUBMIT` makes the platform issue by itself (`MyDataConfig.issues_automatically()`: the receipt at shipment, the credits on refunds); with it off the same work is done by the order's admin actions ("Issue receipt (myDATA)", "Issue credit documents (myDATA)"), which run whenever myDATA is ready. `MYDATA_INVOICE_SERIES_PREFIX` and `MYDATA_CREDIT_SERIES_PREFIX` name the two series (`GRVP-2026`, `GRVC-2026`); the credits have their own gapless counter (`CreditCounter`, numbers `CRD-YYYY-NNNNNN`).

**The receipt is issued when the order ships.** ΕΛΠ art. 13(a): a retail receipt for goods is issued "at the delivery or the start of the shipment". The trigger is `order_shipped` (status SHIPPED, which the carrier polls and webhooks reach through `update_order_status`, cash-on-delivery orders included): `handle_order_shipped` dispatches `generate_order_invoice` when `receipt_issuable(order)` holds (a RECEIPT order at SHIPPED, DELIVERED, COMPLETED or RETURNED) and `issues_automatically()`. Exactly once: `generate_invoice` returns the existing row, `submit_document` returns the existing MARK, and a receipt that already has one is not dispatched again. The payment rows are the ones `order/mydata/builder.py` always emitted (card/online, cash for COD, the gift-card share under its own configurable code). A refused or unanswered receipt is **never emailed**: the customer's email (`send_invoice_email`, "Receipt ...") goes out only after AADE registered it and the PDF carries the MARK; staff resubmit with "Send receipt to myDATA". The 228 duplicate-uid MARK recovery through `RequestTransmittedDocs` is unchanged.

**The receipt is for the consideration actually paid: net of money returned BEFORE it.** A retail receipt (11.1) documents the sale at shipment; a credit document (11.4) only corrects a receipt that exists (it relates to its MARK). So money returned before the receipt is issued reduces the receipt and gets NO credit document: `order.invoicing.refunded_before_receipt` = the REFUND rows on the payment ledger (less REFUND_REVERSAL, a refund the provider reported failed) plus the value returned onto gift cards and points (`PaymentLedger.stored_value(...).returned`). `_compute_vat_breakdown` takes that gross off the rates exactly as a credit would (`credit_documents.credit_breakdown`: in proportion to what each rate holds, to the cent, the net derived cumulatively), so the PDF, `Invoice.total`, `vat_breakdown` and the myDATA payload (`_receipt_lines` uses the frozen breakdown, one line per rate, when the order's own lines no longer add up to the receipt) agree. A refund AFTER the receipt row existed gets its credit as before: `create_pending_credits` only reads REFUND rows created at or after `invoice.created_at`, which is the other half of this rule. If everything was returned before issuance there is no consideration left and no receipt: `generate_invoice` refuses with `ReceiptNotIssuable` ("refunded in full before its receipt was issued"; the shipment task logs it and issues nothing; an existing receipt is never affected). **Value returned onto gift cards and points is credited like a refund.** After the receipt exists, a settled `STORED_VALUE` `RefundAttempt` (value-based refunds, section 7.2) reverses the sale just as a provider REFUND row does, so `create_pending_credits` is the ONE path for both: it credits each such attempt for what its rows actually returned (`PaymentLedger.stored_returned_by`: the gift-card credits plus the restored points at what they were worth, not the amount claimed), as a `RetailCredit` of reason REFUND linked by `refund_attempt` (one document per attempt, exactly one of `refund` / `refund_attempt`, enforced by the `credit_refund_iff_reason_refund` check), VAT split by `credit_breakdown`, capped at what the receipt still holds. Its moment is the first row naming the attempt (`stored_returned_at`), compared with the receipt row's creation like a provider refund's: before it the receipt was netted (no credit). `order_stored_value_returned` triggers the reconciliation. A chargeback still has none.

**One cut-off: the receipt row's creation.** A NEW receipt nets everything returned so far. A row that already exists (a retry after a failed render, or `force=True`) keeps the figures it was issued with, MARK or not (`_persist_invoice_row`), so money returned since is credited by `create_pending_credits` and never also subtracted. The full-refund guard (`_refuse_if_fully_refunded`) applies to every receipt without a MARK, on the normal and the `force` path, and for an existing row counts only what was returned before the row was created (`PaymentLedger.refunded(before=...)`, `stored_value(before=...)`); a receipt with a MARK is frozen.

**A chargeback owes a credit document only when the goods came back, and staff say so (decision M6, refined).** A lost dispute is the card network taking money back, not the merchant reversing the sale, so the receipt stands when the customer kept the goods. When they came back or never arrived the sale is reversed in fact, and art. 12(4) (a credit "in the case of discounts or refunds") applies. Nothing on the ledger tells the two apart, so an order with a CHARGEBACK row offers the "Settle chargeback (goods)" action: «Το προϊόν επιστράφηκε / δεν παραδόθηκε» or «Ο πελάτης κράτησε το προϊόν». `order.chargebacks.decide_chargebacks` writes one `ChargebackDecision` per CHARGEBACK row (no row = undecided), for every chargeback of the order, with `decided_by`, `decided_at` and a note in the order history. Returned goes through the one credit path: `create_pending_credits` credits each such chargeback once (`RetailCredit.reason` CHARGEBACK, `chargeback` one-to-one with the ledger row), for what the chargeback took, capped at what the receipt still holds (`_capped_to_receipt`; a receipt with nothing left is refused with a note, as for a refund), on the same cut-off as a refund with the decision as its moment: a decision made before the receipt row existed nets the receipt (`PaymentLedger.chargebacks_returned` inside `refunded_before_receipt`, so a full return means no receipt), one made after it is credited. Kept credits nothing. Idempotent. A returned decision can be changed back only until the order has a receipt row (after it the receipt was netted or a credit is owed, and `ChargebackDecisionError` refuses); kept can still become returned. An undecided dispute is the dashboard counter "Lost disputes to settle" (`OrderQuerySet.awaiting_chargeback_decision`, list filter `?chargeback=undecided`), and the lost-dispute email says to decide. The decision does not touch the ledger, `payment_status` or loyalty. A chargeback recorded before the decision is not netted into a receipt (`refunded_before_receipt` counts only decided rows).

**Credit documents.** `order.credit_documents.create_pending_credits(order_id)` is the one reconciliation (under the order row lock, idempotent) and the `issue_pending_credits` task wraps it and also resubmits any credit not registered yet. It is dispatched by:

- `handle_order_refunded`: every refund path ends in `PaymentLedger.record_refund` and signals `order_refunded` once per REFUND row (in-app refund, the staff "Refund" action, `cancel_order`, Stripe `charge.refunded`, Viva reversal 1797), so each refund gets one document, `RetailCredit.refund` being one-to-one;
- `handle_order_post_save` when `payment_status` becomes CANCELED (an order closed unpaid, `Order.settle_unpaid_payment`: the refused or uncollected COD parcel that came back RETURNED);
- `decide_chargebacks` when staff decide the goods came back (`request_credit_documents`, the same `MYDATA_AUTO_SUBMIT` gate);
- the receipt's own success, for the credits that waited for its MARK.

What it creates: one credit per ledger REFUND row recorded after the receipt row existed, for exactly the refunded amount; one per CHARGEBACK row whose goods staff decided came back, decided after the receipt row existed (capped at what the receipt holds); plus, once the order's payment is REFUNDED or CANCELED, ONE settlement credit (`reason=SETTLEMENT`) for what the receipt still holds and no credit covers (the whole receipt of a returned COD parcel; the gift-card share of a fully refunded order). Nothing is created until the receipt is CONFIRMED (it has a MARK and was not canceled): a credit cannot be linked without it. A refund recorded before the receipt existed needs no document (nothing was issued to credit), and neither does a cancellation before shipment: `cancel_order` accepts only PENDING and PROCESSING, so it always precedes the receipt. After shipment the order is RETURNED, and the refund (or the COD settlement) is what credits it.

**VAT split.** The receipt froze its gross and net per rate (`Invoice.vat_breakdown`, shipping and fees included). A credit spreads its amount over the rates in proportion to what each still holds (`order.discounts.allocate_capped`), so a partial refund of a mixed 24%/13% order credits both. **Our cap is the only over-credit guard.** Because AADE cannot link an 11.4 to its receipt it cannot check a credit against the receipt's total (error 319 belongs to linked credits), so `credit_breakdown` is the sole protection: a credit's NET is derived cumulatively, from what the rate's total credited gross splits to (`order.taxation.split_gross`), capped at the receipt's net for the rate, less the net already credited. Any number of partial credits stay under the receipt and the last lands exactly on it. A credit the receipt cannot hold is not sent: a staff `OrderHistory` note says why.

**CancelInvoice** is lawful only for a document that was never used — never delivered, sent or made available to the customer (Α.1138/2020 annex, «μη χρήση παραστατικού»; AADE Q&A 2025 §8 Q24); once it has reached the customer the correction is a credit document. Neither the law nor the API sets a time limit. Receipts and credits are emailed as soon as AADE registers them, so in practice a credit document is the tool. The admin actions ("Cancel receipt in myDATA (issued in error)", and "Cancel in myDATA" on the Retail Credit Documents list) do not enforce the "never used" rule yet; the guard is listed in issue #197.

**Invoices (B2B) are not issued by the platform.** From 2.11.2026 AADE decision Α.1128/2025 art. 1(2) requires 1.1 and 5.1 documents to come from a licensed provider or AADE's free timologio app; an ERP-issued invoice counts as not issued (circular Ε.2004/2026). A `document_type=INVOICE` order therefore produces no `Invoice`, no PDF, no email and no myDATA submission (`receipt_issuable` refuses it; the 1.1 builder is gone). The checkout still collects the buyer's company details; the order admin's "Billing / Invoice" tab shows staff what timologio needs (`OrderAdmin.timologio_summary`: the identity and the VAT rows of the order, from `order/taxation.py`) and records what they issued (`Order.timologio_invoice_number` / `_mark`, `timologio_credit_number` / `_mark`). The order confirmation says an invoice was *requested* ("Invoice request"), never that one is attached.

**Settled questions** (research of 2026-10-14, primary sources in issue #197): an e-shop needs a ΦΗΜ or a licensed provider for its retail receipts, so the ERP channel needs a recorded exemption (above); the law sets no deadline for a retail credit document (the "15th of the next month" rule is for credit invoices, ΠΟΛ.1003/2014 §11.2.10, and applies to retail only by analogy); `CancelInvoice` has no time limit, only the "never used" condition.

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
| Operational mail to a store's operators goes through `send_ops_alert(kind=...)` only (the store's `OPS_ALERT_<KIND>_ENABLED` switch lives in that one gate); a kind a store may mute has a dashboard counter by the same rule as its email | §5.4 above |
| "Follow up" is computed (`OrderQuerySet.needing_follow_up`), never stored, and its unpaid-online exclusion is `awaiting_online_payment()` (settled statuses), not `Order.awaits_online_payment` | §2.3 above |
| One order per provider payment: `Order.payment_id` is unique when non-empty (`order_payment_id_unique`), and `create_order_from_cart` answers a repeat of the same cart's intent with the first order (found before the provider is asked, and again under the cart lock, both before cart validation) and refuses an intent of another cart or of a canceled / deleted order, a unique-index race between two carts included, all as `payment_verification` | §3.1 above |
| No provider HTTP call inside the order-creation transaction (nor in a view-level `atomic`): the intent is read first, and the transaction only re-compares its amount and currency with the total computed under lock | §3.1 above |
| Product rows are locked in ascending pk order, in one statement, before the first row that references a product is inserted (`StockManager.lock_products`); a cart-item lock never joins the products (`of=("self",)`) | §3.1 above |
| Every checkout-created order since `0059_orderattribution` has exactly one `OrderAttribution` row (`direct` when there was no signal); for a checkout order a missing row means "older than attribution", never "direct"; `source_type=agent` only with the gateway's `X-Internal-Gateway` secret | §3.4 above |
| A cart records its first touch (`CartAttribution`) only through `CartAttributionService.capture`, called before the line is written: an empty cart takes it, a cart holding lines keeps it, a merge keeps the earlier; both order creation paths copy it to `OrderAttribution.cart_source*` because the cart is deleted with its lines | §3.4 above |
| An issued tax document never re-reads the catalogue's VAT: invoices and myDATA split each line at `OrderItem.vat_rate`, snapshotted at creation, and tax shipping and the payment fee at the rate(s) of the goods (`order/taxation.py`); `Invoice` totals and the myDATA `invoiceSummary` come from the same lines | §3.5 above |
| The default cache fails open on a Redis outage (reads miss, writes no-op — `core/caches.py`), but never for keys under `STRICT_KEY_PREFIXES`: the `strict:` throttle namespace, allauth's rate limits, TOTP replay protection, the IdP's single-use codes and WebSocket tickets. The checkout, payment, coupon and gift-card throttles (`core/api/throttling.py`, `fail_closed = True`) therefore DENY with 429 during an outage, while browsing/search/cart throttles and DRF's default `anon`/`user` budgets allow | `core/api/throttling.py` module docstring |
| The tenant resolve payload and domain set are invalidated by `Tenant.cache_generation`, never by a Redis delete: it is part of their keys, and `Tenant.save`, every `TenantDomain` write and the folded pay-way / agent-setting writes bump it in the database inside the write's transaction. A delete during an outage is a fail-open no-op, so purge-by-delete left the pre-write payload served for up to its TTL; strict keys would have made the READS raise, and resolve is on every storefront request | `tenant/cache.py` module docstring |
| Gift-card value a staff member creates in one step (a new card, or a positive balance adjustment) is capped per store by `GIFT_CARD_STAFF_ISSUE_LIMIT` (default 500, `0` = off). Above it only the store's OWNER or a platform superuser may do it (`GiftCardService.over_staff_limit`; an ADMIN may not); the ledger row says "above the staff limit ... authorised by the owner", `created_by` names the person, and an adjustment adds an admin log entry. A refusal is logged. Enforced in `giftcard.admin` (the add form and `adjust_balance`). Steps repeat, so one staff member is also capped over any rolling 24 hours by `GIFT_CARD_STAFF_DAILY_LIMIT` (default 1000, `0` = off): the positive ISSUE and ADJUST rows with their `created_by` in the last day plus the new amount (removals do not offset it; purchased cards have no `created_by`). Same authority rule and the same audit ("above the staff daily limit ... authorised by the owner", admin log on an adjustment). The total is read and the row written in one transaction under a per-member advisory lock (`GiftCardService.lock_staff_issuing`), so two parallel requests cannot both pass | `giftcard/services.py`, `giftcard/admin.py` |
| The nightly demo reset returns the published demo gift card (`GIFT_CARD_CODE`) to its issued value with one `ADJUST` ledger row, so the ledger sum stays the balance and every guest spend stays on the card's history; only on `is_demo` tenants, and silently (no mail, no dispatch) | `devtools/demo_account.py: _restore_gift_card` |
| "Free above X" means one value for shipping and for the pay-way fee: the products after discounts, excluding shipping and fees (`OrderService.threshold_value`), passed by every caller of `shipping_cost` and `calculate_payment_method_fee` | §5.0 |
| A payment is bound to what it was made for: every PaymentIntent carries server-set `purpose` / `subject` / `tenant_schema` metadata, payment-first order creation refuses an intent that is not `cart` + this cart + this tenant (or that a gift-card purchase holds), and the polled status path settles only through `handle_payment_succeeded` | §3.1, §4.1 |
| A confirmed charge settles an order only for its own total: Stripe's collected amount and Viva's amount AND currency are compared (a missing one refuses), a mismatch is booked, flagged (`AMOUNT_MISMATCH_FLAG`), alerted and never auto-canceled | §4.1, §4.3 |
| A reversal of a gift-card purchase's payment (refund, claim, chargeback, Stripe or Viva) voids the unspent card and records the spent part | §4.2.1 |
| `Order.payment_status` / `payment_id` are read-only in the admin, and the owner PATCH/PUT cannot change a priced order's country or region | §7 |
| ACS voucher mint uses 3-phase claim → API → persist with 300s TTL | `project_acs_voucher_orphan_prevention.md` |
| `Order.objects.filter(pk=...).values(...).first()` — NOT `refresh_from_db(fields=...)` | `project_order_state_machine_invariants.md` |
| `_suppress_customer_status_notifications` on chained transitions | `project_order_state_machine_invariants.md` |
| Webhook payment_status writes never regress a SETTLED state (`SETTLED_PAYMENT_STATUSES` = COMPLETED/REFUNDED/PARTIALLY_REFUNDED/CANCELED). Stripe/Viva events are unordered + may duplicate, so `handle_payment_failed`/`_handle_payment_failed` skip when already settled, and `handle_payment_succeeded`/`_handle_payment_created` skip when already REFUNDED/PARTIALLY_REFUNDED/CANCELED. Reversal (COMPLETED→REFUNDED) is left intact. | `order/enum/status.py` `SETTLED_PAYMENT_STATUSES` |
| `order_refunded` always carries `amount`, and the gift-card redemption and the spent points go back only as the value of a refund beyond the provider's money (`refund_order`, value-based, §7.2) or on a cancel, never because the provider's share was refunded; the points an order earned are reversed once, when `payment_status` is REFUNDED. A Viva 1797 is applied from the REFUND transaction (`F`, refund/void type, negative amount), not from a REFUNDED status that Viva never reports | §4.3, §7.2 |
| Money moved for an order is recorded on the append-only `PaymentTransaction` ledger, once, keyed on the provider's own id (`UNIQUE (provider, provider_reference, kind)`). What can still be refunded is read from it under the order row lock, and `PaymentLedger.refund_status` is the ONE place that decides REFUNDED versus PARTIALLY_REFUNDED: no path writes either status for itself, and none re-derives it from `Order.amount_due` (the amount owed at creation) or from a provider's refund `status`. A refund is as many `refund_order` calls as it takes, never past the remainder | §7.2, §7.3 |
| The platform issues retail myDATA documents only: an 11.1 receipt when a RECEIPT order ships (`order_shipped`, exactly once) and an 11.4 credit per ledger REFUND row of an order whose receipt has a MARK, plus one settlement credit when the order closes without a sale. `document_type=INVOICE` orders submit nothing: invoices are issued by hand in timologio (AADE decision Α.1128/2025 art. 1(2)). A document AADE did not register is never emailed. A credit's net is cumulative so partial credits never pass the receipt's net (our own cap; AADE does not check) | §7.4 |
| `Order.amount_due` is what the customer OWES, fixed at creation (`calculate_order_total_amount()`); it is never read as money collected. What is still to be paid is `Order.remaining_amount` (`pricingBreakdown.remainingAmount`): `amount_due` until `payment_status` is settled, 0 after, and never derived from the ledger (a settled order can hold no ledger row). Whether an order is paid is `payment_status` alone (`Order.is_paid`, the `is_paid` filter). It was called `paid_amount` until `0070_order_amount_due` (the columns were dropped by `0071_order_drop_paid_amount_columns`, and `paidAmount` is gone from the API), and every reader that took it for collected money was wrong (`remainingAmount` was 0 on every order, the filter matched every unpaid online order) | §7.3 |
| A Stripe refund carries `idempotency_key=order-<id>-refund-<n>`, `n` counted from the ledger under the order lock. A refund the provider did not take on (`requires_action`, `failed`, `canceled`) records nothing and never moves the order to PROCESSING | §7.2 |
| `refund_order` returns a `RefundResult` (what actually moved), never a yes/no: a split refund declined part-way keeps the shares already made and reports them, and every caller (the staff action, `cancel_order`) says so instead of "nothing was refunded" | §7.2 |
| The refund confirmation email is one per REFUND ledger row, claimed by inserting a `RefundNotification` BEFORE it is sent (unique per refund), and the claim is released when the send fails for good. It is never decided by comparing counts: that let two workers both send and let one task swallow another refund's email | §7.2, §7.3 |
| A COD order's payment status and its `COD_COLLECTION` ledger row are written in ONE atomic step under the order lock (`OrderService.settle_cod_payment`), the status read under that lock. A failed insert rolls the status back, so a payout retry never skips an order that is paid but unrecorded | §4.4 |
| The "shipped" email/toast fires only at genuine SHIPPED (status==SHIPPED **and** tracking present), never at voucher-mint; PROCESSING is internal-only (no customer email/toast). `send_shipping_notification_email` self-gates. | §6.1 above |
| Admin-WYSIWYG fields in emails render `\|safe` (.html) / `unescape(strip_tags())`+`\|safe` (.txt — Django autoescapes .txt too) | `CLAUDE.md` "Transactional email rendering" |
| ACS COD numeric fields use Greek-locale (comma decimal) | `project_acs_cod_locale.md` |
| Don't import from `'#shared/...'` in app/ or server/ | `feedback_no_shared_imports.md` |
| Don't override generated Zod / OpenAPI types in Nuxt | `feedback_no_local_schema_overrides.md` |
| OpenAPI regen workflow after Django serializer changes | `project_schema_regen.md` |

## 9. Common task playbook

### 9.1 Adding a new order email

1. Create the `.html` + `.txt` pair under `core/templates/emails/order/`.
   `Registry.freeze` refuses a template in that directory no type names.
2. In `order/emails.py` add its `build_context(obj, env)` (runs under
   `_email_language`, returns only the keys the templates read plus the
   subject's named fields, never calls `build_email_context`), its
   `sample(env)` and a `_customer_email(...)` / `_staff_email(...)`
   declaration with the subject msgid, the kind of requirement
   (`required`), the gates and the `list_id`. Run `makemessages` and
   translate the new msgids (title, description, subject).
3. Add a Celery task in `order/tasks.py` mirroring `send_payment_failed_email`:
   - Idempotency reservation helper (`_reserve_<task>_email`).
   - `send_email("<type key>", obj)`, mapping the returned `Delivery` list
     with `was_sent`: no subject, context, recipient, headers or
     `translation.override` of its own.
   - Catch exceptions; on permanent failure, release the reservation.
4. Wire from a signal handler in `order/signals/handlers.py` via `transaction.on_commit`.
5. Add a case in `tests/integration/emails/test_golden_render.py` (and its
   golden files) and one in
   `tests/integration/core/email/test_preview_matches_send.py`; a
   regression test in `tests/integration/order/test_signals.py`.

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
