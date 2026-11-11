# Order system: shipping rules, pricing and alerts

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 5. Shipping integrations

Each carrier implements `ShippingCarrierInterface` in
`src/shipping/interfaces.py` and registers with `@register_provider`
(imported in its `AppConfig.ready()`). Two courier carriers today, plus
the API-less `flat_rate` adapter (`src/shipping/carriers/flat_rate.py`).

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
  provider supporting `kind`, `is_kind_enabled(kind, country_code=...)` (ACS
  lockers: active stations synced for that country), and an active rate row
  for that country — or `ShippingUnavailableError`.
  `assert_available(...)` adds the weight-cap check
  (`ShippingWeightExceededError`); both are used directly by a
  free-shipping promo, which must not let an unavailable or over-weight
  option through just because it charges nothing.
- **One basis for every "free above X".**
  `ShippingRate.free_shipping_threshold`
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
  it. `src/shipping/delivery.py` owns the arithmetic: dispatch is the
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

### 5.3 Adding a new carrier

1. Create `shipping_<provider>/` Django app.
2. Implement `ShippingCarrierInterface` (`src/shipping/interfaces.py`):
   - The abstract methods: `create_shipment`, `cancel_shipment`, `has_label`,
     `fetch_label_bytes`,
     `fetch_tracking_events` (return `[]` when events arrive by webhook),
     `shipment_for_order`,
     `serialize_shipment`, `validate_order_payload`.
   - `dispatch_create_shipment_task(self, order, *, schema_name: str | None = None)`
     — enqueues the
     Celery task that mints the voucher. `ShippingService` always passes
     `schema_name` (captured
     before `on_commit`); accept it and stamp it on the task
     (`headers={"_schema_name": ...}`), or the
     worker runs under the wrong tenant. The default is a no-op for carriers
     with no async step.
   - Webhook-based carriers handle events in their own service (BoxNow:
     `BoxNowService.apply_webhook_event`); it is not part of the interface.
3. Decorate the adapter with `@register_provider` and import it in the app's
   `AppConfig.ready()`.
4. Add `ShippingProvider` row in DB (admin or migration).
5. Implement `_apply_order_status_transition` if your carrier emits state
   events.
   If a canceled shipment leaves the order open (BoxNow), override
   `canceled_shipment_q` so the order shows under follow-up
   ([§2.3](state-machines.md)).
6. Wire the COD path if applicable (see ACS for the pattern).

### 5.4 Operational alert emails (`src/shipping/alerts.py`)

Merchant-operations mail is a set of email types (`ops.<kind>`, declared in
`src/shipping/emails.py`, `src/shipping_acs/emails.py`, `src/order/emails.py`,
`src/giftcard/emails.py` and `src/product/emails.py`; see `docs/emails.md`,
Operational alerts). They go through `send_email` to `store_operators()` (the
store's contact and owner addresses; no `ADMINS` fallback, a store with
neither skips the mail, [§6.2](emails-and-notifications.md)) and are **per
store, per kind**. The registry
is the whole list:

| Type | Sender | Where the work shows when the email is off |
|---|---|---|
| `ops.stale_shipments` | `check_stale_acs_shipments` digest, 09:00 | Dashboard "Stale ACS shipments" (`shipping_acs.attention.stale_shipment_q`) |
| `ops.unremitted_cod` | `alert_unremitted_cod_payouts`, 03:00 | "COD cash ACS has not paid out" (`unremitted_cod_shipments`) |
| `ops.unattributable_cod_payouts` | `AcsService._alert_admins_unmatched_payouts` | "ACS payouts matching no shipment" (`unattributed_payouts`: every payout with no shipment and no reconciliation, no time window) |
| `ops.acs_voucher_request_unknown` | `AcsService._alert_voucher_request_unknown`, once when the state begins | "ACS voucher requests to check" (`voucher_requests_unknown`), until staff adopt or request again; plus the order note |
| `ops.acs_unrecognised_vouchers` | `AcsService._raise_unrecognised_vouchers`, the first time ACS names each voucher | "ACS vouchers matching no order" (`unrecognised_vouchers`), until staff adopt, delete or mark it checked |
| `ops.shipment_rejected` | `alert_admins_shipment_creation_failed` | An `OrderHistory` note on the order, written whether or not the email is on |
| `ops.webhook_failed` | `alert_admins_webhook_processing_failed` | The ERROR log only |
| `ops.payment_after_cancel` | `OrderService.record_payment_after_cancel` | "Canceled orders to refund" (`OrderQuerySet.refund_owed`), until refunded; plus the order note |
| `ops.overcharged` | `PaymentLedger.sync_overcharge` | "Orders paid twice" (`OrderQuerySet.overcharged`), until refunds bring the ledger back; plus the order note |
| `ops.refund_failed` | `handle_stripe_refund_failed` | A NOTE on the order and a `REFUND_REVERSAL` row on its ledger |
| `ops.review_pending` | `product.tasks.send_review_pending_alert`, queued after commit by `ProductReviewWriteSerializer.save` | Dashboard "Reviews to approve" and the sidebar badge (`pending_reviews`) |

- **One gate.** Every sender calls `send_email("ops.<kind>", ...)`, which
  skips the mail when the store switched the type off or has no recipient.
  No carrier module imports `send_mail`
  (`tests/integration/emails/test_ops_alerts.py` enforces it). The stale
  digest used to call `send_mail` itself, so it could not be muted.
- **One switch per type**, an `EmailSwitch` row (`docs/emails.md`, The
  store's switch): a store opts OUT, so a type added later reaches every
  store. The `OPS_ALERT_<KIND>_ENABLED` settings it replaces were imported
  by `emails/0003`. Adding an alert means declaring its type in the owning
  app's `emails.py`, with a template pair and a golden file. The removed
  senders (unprinted vouchers, pickup list blocked) are not types: they no
  longer email.
- **Switching off loses nothing.** A switched-off digest keeps no claim
  (`stale_alert_sent` is released), so the parcels appear in the first
  digest after the email is switched back on. The money alerts and the
  stale digest may be switched off only because the same rule feeds a counter
  in
  `attention_counts()` (`admin.dashboard.store.queries`); each counter
  opens the list through a filter
  (`attention=stale|voucher_unknown|unremitted_cod`
  on the ACS shipment list, `resolved=no` on the ACS vouchers matching no
  order, `in_force=yes` on the ACS voucher holds (a state with no email of
  its own, [§5.1](shipping-acs.md)), `unattributed=yes` on the payout list,
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
- To keep one alert on a store, switch the others off on the store's
  email page; the recipients stay `store_operators()`.
