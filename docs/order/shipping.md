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
  under the shipment lock. That is an online pay way whose payment is not
  settled (`SETTLED_PAYMENT_STATUSES`): an order paid and then partly
  refunded still gets its voucher (#649). An online-paid voucher carries no COD amount,
  so a voucher minted for an unpaid card order would hand the goods over
  for free (prod order 304 was one admin click away, 2026-09-24). The
  tasks return `{"status": "awaiting_payment"}` with no retry and no
  alert; the admin buttons ("Issue ACS voucher now", "Create BoxNow
  parcel now") refuse with a message, because the task's refusal never
  reaches the page.
- **No shipment for an order that ships nothing.** `Order.ships_nothing`
  (`order.managers.order.ships_nothing_q`): the order is CANCELED or
  REFUNDED, or it was paid online and the store keeps nothing of the
  payment (`PAYMENT_STATUSES_KEEPING_NOTHING`: a full refund leaves the
  order PROCESSING). Both mint choke points raise
  `shipping.exceptions.ShipmentNotNeededError` for it (ACS also for a
  shipment it retired, `shipping_acs.attention.needs_no_voucher`; BOX NOW
  first finishes a canceled order's cancel), the tasks return
  `{"status": "not_needed"}` with no retry and no alert, and both admin
  buttons refuse with a message. A retry that waited out a cancel in its
  backoff therefore mints nothing. ACS asks for the store's credentials
  only after that refusal, so an unconfigured store still answers
  `not_needed`. Where BOX NOW may already hold a parcel (a creation of
  uncertain outcome settled by the sweep, or one whose answer arrives
  after the order stopped shipping), the parcel is cancelled at BoxNow and
  the shipment recorded CANCELED, as for a canceled order
  ([BOX NOW](shipping-boxnow.md)). No receipt with movement indication is
  issued for such an order (`receipt_issuable`).
- **The receipt follows the voucher, never the other way round.** Every
  path that makes a voucher or parcel live records it with
  `Order.add_tracking_info`, and recording it issues the receipt with
  movement indication (`issue_receipt_for_pickup`), also over a number
  staff typed before the carrier held the parcel, the voucher's own number
  included, for a
  store whose receipts the platform issues and that has set its dispatch
  point ([§7.4](fiscal-movement-receipts.md)), dated the day the voucher is
  created.
  Only a voucher or parcel the carrier holds counts
  (`ShippingCarrierInterface.holds_the_parcel`, asked through
  `ShippingService.carrier_holds_the_parcel`): a tracking number staff type
  on an order whose carrier holds none issues nothing.
  Nothing in shipping waits for myDATA: a receipt AADE does not answer for
  is sent again, and once its parcel leaves unregistered, with AADE's
  loss-of-connection indication. A voucher made again after the receipt
  was issued (a new locker or address, or days later) is flagged to staff
  when the parcel leaves.
- **One HTTP retry rule.** Both clients build their `requests` session
  with `carrier_session` (`src/shipping/carrier_http.py`), whose
  `CARRIER_RETRY` sends a request again only when it never connected
  (any method) or when it is a GET (after a 5xx or a lost answer too). A
  POST or PUT that may have reached the courier is never sent again: the
  client raises its own error and the caller decides (§5.1, §5.2). Every
  ACS call is a POST, so ACS only gets a failed connect re-sent.
- **Lock order: the order, then the shipment.** A carrier path that
  needs both rows (it reads the order under the lock, writes it, or
  inserts a row that references it) takes them through
  `shipping.locks.lock_with_order`, and nowhere else: the voucher and
  parcel creation's claim and save, the tracking poll, the BoxNow webhook
  and settle sweep, ACS's adopt / request again / close and "COD received
  outside ACS". A joined `select_for_update().select_related("order")`
  locks the shipment's row first, the opposite order, and two
  transactions that take the same rows in opposite orders wait on each
  other until PostgreSQL aborts one (#613: an ACS poll and a staff
  cancel, reproduced with two connections). A path that touches the
  shipment alone locks the shipment alone (`AcsService.cancel_voucher`,
  `BoxNowService.cancel_shipment`) and must not write the order in that
  transaction. `tests/unit/shipping_acs/test_lock_order.py` and
  `TestEveryPathLocksTheOrderFirst` (BoxNow) fail for a path that goes
  back to the join.
- **No carrier HTTP call while a row is locked.** Every carrier call is
  made between two short transactions (claim, call, save). An order
  cancel keeps to it too: it commits, and only then asks the courier
  ([§7.1.2](cancel.md)).
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

`shipping.ShippingRate` (tenant schema) is the single source of a
store's shipping price, availability and weight cap, one row per
`(provider, zone, kind)`: a rate belongs to a `ShippingZone`, a whole
country or some of its regions, and which zone a destination is priced
by is [§5.5](shipping-zones.md). It replaced a global
`CHECKOUT_SHIPPING_PRICE` / `FREE_SHIPPING_THRESHOLD` /
`BOXNOW_*` / `ACS_*` Setting sextet that priced every destination the
same and had nothing to say about whether a store shipped there at
all — the gap that blocked offering BoxNow-to-Cyprus without also
accepting Cyprus orders at the Greek flat rate.

- **`ShippingService.active_rate(provider_code, kind, country_code,
  region_id)`** is the single gate: an active provider, a registered
  adapter, the provider supporting `kind`, `is_kind_enabled(kind,
  country_code=...)` (ACS lockers: active stations synced for that
  country), and an active rate in the zone the destination resolves to
  ([§5.5](shipping-zones.md)) — or `ShippingUnavailableError`, or its
  subclass `ShippingRegionUnavailableError` when the carrier has that rate
  elsewhere in the country but not in the region's zone.
  `assert_available(...)` adds the weight-cap check
  (`ShippingWeightExceededError`); both are used directly by a
  free-shipping promo, which must not let an unavailable or over-weight
  option through just because it charges nothing.
- **One basis for every "free above X".**
  `ShippingRate.free_shipping_threshold`
  and `PayWay.free_threshold` are both compared with
  `OrderQuoteService.threshold_value(cart_total, discount_total)`
  (`src/order/checkout/quote.py`): the products after promotion and
  coupon discounts, excluding shipping and the pay-way fee (loyalty
  points and gift cards are payment, not discounts, and do not move it).
  `OrderQuoteService.quote` passes it to `shipping_cost` and
  `calculate_payment_method_fee`, and order creation and
  `POST /cart/quote` price through that one function ([§3.0](creation.md)), so they agree for the same pricing
  inputs. The inputs can change between two requests (a price, a
  promotion, a rate), and then the later total differs: every order is
  created with the quote's `expectedTotal`, and only at that total. The cart response
  reports it as `freeThresholdValue`, and `/shipping/options` takes it as
  `orderValueAmount`; a client shows "X more for free" from that number,
  never from `totalPrice`. (Before, the fee compared items + shipping and
  free shipping the gross total, so a EUR 39.98 cart with both thresholds at
  EUR 40 paid shipping but not the fee.)
- **`ShippingService.quote(...)`** is `assert_available` + pricing, and
  returns a `ShippingCharge`: the price, and `waived_by` when a rule took
  a real price to 0 (`promotion` for `free=True`, a free-shipping
  promotion, which still runs the gate; `threshold` for the rate's own).
  A rate whose own price is 0 has nothing to waive, so its `waived_by`
  stays unset. `cached_only=True` (order creation, under its locks) never
  asks a carrier: it reads what `resolve_live_price` had the adapter
  resolve before the locks, and raises
  `ShippingLivePriceUnresolvedError` when the adapter holds no answer.
  The order is: the promotion, then
  the rate's own `free_shipping_threshold`, then the carrier's
  `live_quote()` hook (ACS's weight-banded `ACS_Price_Calculation`,
  gated on `ACS_DYNAMIC_PRICING_ENABLED`; every other carrier returns
  `None` and the rate's stored `price` wins; ACS caches its answer per
  country, region and weight bracket: a price for five minutes, a
  failed call for one, during which the stored rate is charged and ACS
  is not asked again), and is
  `OrderQuoteService.shipping_cost`'s only pricing path — no generic
  fallback survives it.
- **`ShippingCarrierInterface.live_quote(rate, country_code, region_id,
  weight_grams, currency)`** replaces the old
  `calculate_shipping_cost`/`free_shipping_threshold` pair. A
  transient failure returns `None`, never raises — the stored rate
  price is the safety net.
- **`/shipping/options`** requires `country_code` (400 without) and
  resolves the matrix from the active rates of the destination's zone:
  the optional `regionId` picks a region zone, and without it the
  country's whole-country zone answers ([§5.5](shipping-zones.md); a
  `regionId` the country does not have is a 400 `region_invalid` or
  `region_country_mismatch` on `regionId`); each row
  carries `country_code`, `max_weight_grams` and `exceeds_max_weight`
  — an over-cap option (a heavy Cyprus BoxNow cart) is still listed,
  flagged, so the storefront can disable it with a reason instead of
  the step silently having one fewer card. It prices on the caller's
  `orderValueAmount`, with no promotion waiver; the
  `shippingOptions` of `POST /cart/quote` are the same rows
  (`available_options`) priced as an order would be, each with
  `waivedBy` ([§3.0](creation.md)).
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
  `OrderQuoteService.free_shipping_progress(cart, promotion_result)` decides it
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
  the engine's own simulation adds (`promotion.engine.teasers.teaser_cost`).
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
  purges it and declares `ShippingRate`, `ShippingProvider`,
  `shipping_acs.AcsStation` (the locker gate) and
  `shipping_boxnow.BoxNowLocker` (the locker list) as `invalidated_by`;
  the catalogue syncs mark it for the rows they deactivate. A
  carrier's credentials live on the `Tenant` row, which no surface watches,
  so connecting or removing one shows after the TTL or a manual purge of
  the `shipping` surface.
- **`GET /shipping/product/<product_id>`** is the product page's
  "shipping from X": `ShippingService.product_shipping_from(product)`
  prices a cart holding that one product and nothing else, as checkout
  would, for a shopper not known yet:
  - to `initial_country_code()`, the country checkout preselects;
  - at `OrderQuoteService.threshold_value` of the product's
    `final_price` after the automatic promotions such a cart gets
    (`PromotionEngine.evaluate_single_unit`: the cart's own
    classification, stacking and clamp over a one-line cart; no code,
    and no `first_order_only` promotion, as for any anonymous cart), so a
    free-shipping promotion covering the product waives every carrier and
    a carrier's threshold is judged after the discount;
  - through `available_options` with the parcel the cart would ship (the
    product plus the promotions' gifts, `gift_pairs`): a row flagged
    `exceeds_max_weight` or `exceeds_parcel_size` cannot carry it and does
    not count, and a live-priced carrier (ACS) is asked for the parcel's
    weight bracket as checkout asks it.

  The response is `{estimate}`: the cheapest row's `price` and `currency`,
  `free` (`price` is 0), `countryCode`, and `freeShippingThreshold`, the
  lowest threshold of `free_threshold_rates(country, weight_grams, items)`
  (the carriers that can take it). `estimate` is `null` when the store
  ships nowhere or no delivery it offers can take the product; 404 for a
  product that is missing or inactive. Retail only: a wholesale price
  never reaches it (no shopper). Not on the product serializer, which
  would tie the product cache to every rate. Anonymous, cached by
  `@cache_methods` (`ProductShippingView`); the `shipping`, `products`
  (the product's price, weight and size) and `promotions` surfaces all
  purge it (`PRODUCT_SHIPPING_VIEW` / `PRODUCT_SHIPPING_HANDLER` in
  `src/shipping/cache_surfaces.py`). A live ACS price can move within
  the TTL.
- **`?shippable=true` on `/country`** filters to
  `shippable_country_codes()`; always empty on the platform host,
  which has no tenant schema of its own to hold a rate.
- **`ShippingService.resolve_home_delivery_provider(country_code)`**
  picks the lowest-priority active home-delivery provider that has a
  rate for the country (or, with no country yet, the lowest-priority
  active one regardless), among the carriers checkout's gate takes
  (`carrier_offers`). `OrderQuoteService.quote` resolves it once and
  prices it, and order creation assigns that carrier
  (`CheckoutQuote.shipping_choice`, `_assign_shipping_provider`), so the
  price and the carrier always agree.
- **Release N migration** (`shipping/0011_convert_legacy_pricing_to_
  rates.py`) copies each tenant's existing Setting values — or the
  frozen historical default when a row is missing — into GR-only rate
  rows (`metadata['supported_countries']` when set, else `["GR"]`), so
  no store starts offering a new country just because this shipped.
  The six Setting keys themselves are dropped in release N+1
  (`0014_drop_legacy_shipping_settings.py`, additive-only rule —
  `docs/migrations.md`).
- **Adding a country to an existing carrier** is an admin action, not a
  deploy: add a rate for `(provider, kind)` on the country's zone
  (Settings → Shipping Zones, its Rates tab), creating the zone first
  ([§5.5](shipping-zones.md)).

### 5.3 Adding a new carrier

1. Create `shipping_<provider>/` Django app.
2. Implement `ShippingCarrierInterface` (`src/shipping/interfaces.py`):
   - The abstract methods: `create_shipment`, `cancel_shipment`, `has_label`,
     `fetch_label_bytes`,
     `fetch_tracking_events` (return `[]` when events arrive by webhook),
     `serialize_shipment`, `validate_order_payload`.
   - `dispatch_create_shipment_task(self, order, *, schema_name: str | None = None)`
     — enqueues the
     Celery task that mints the voucher. `ShippingService` always passes
     `schema_name` (captured
     before `on_commit`); accept it and stamp it on the task
     (`headers={"_schema_name": ...}`), or the
     worker runs under the wrong tenant. The default is a no-op for carriers
     with no async step.
   - The admin hooks, so no admin module names the carrier (all default to
     nothing). A carrier that is an app of its own sets `app_label` (the
     sidebar and the ⌘K palette drop its pages while the store has no active
     `ShippingProvider` row for it) and returns its pages from `admin_pages()`
     (`AdminPages`: the Shipping group, the Pickup Points menu, the System
     section's Audit and Reconciliation menus). `shipment_state_variants()`
     colours its shipment states (`admin.displays.SHIPMENT_STATE_VARIANT`
     merges every carrier's). `attention_counts(now)` and `attention_rows(counts)`
     put the work it leaves waiting on the dashboard's "Needs attention" list
     (the same rules as its alert emails, §5.4);
     `attention_models` lists the models whose writes clear the cached counts.
   - The order hooks, so no `order` module names the carrier (all default
     to nothing). `shipment_relation` is the `related_name` of its shipment
     row's one-to-one to `Order` (`acs_shipment`): `shipment_for_order`
     reads it and the order lists join it (`OrderQuerySet.with_shipments`).
     `order_detail_prefetches` lists what the order detail reads of it
     (`for_detail`). `shipment_state(shipment)` is the state the order
     changelist labels, `last_reported_at(shipment)` the last report the
     follow-up rule reads (`order.lifecycle.follow_up`).
     `admin_order_inline()` is the inline the order change page shows for
     its orders; `admin_order_search_fields` and `admin_order_list_filters`
     join the order changelist's. `order_shipment_field()` gives it a typed
     field of its own on the order detail (`OrderShipmentField`: name and
     detail serializer), before the generic `shipment`.
   - The checkout hooks. `checkout_fields()` returns the request fields it
     takes, by name. The ones named in `location_keys` are its pickup
     point: they lead `PickupPointSerializer`, so the quote and the order both
     take them, and the cart digest covers them.
     The rest are options for the order alone and follow `shipping_kind`
     on `OrderCreateFromCartSerializer`. The order view hands what the
     body sent of them to the carrier as the payload. `field_rank` places
     its fields among the carriers' in every API body, the order detail's
     included (BOX NOW 0, ACS 1). `tracking_url_template` (`{number}`) is
     its parcel's public tracking page, the order's `tracking_url` when
     `shipping_carrier` is its `code`.
   - Its API errors subclass `shipping.exceptions.CarrierAPIError` and
     return the courier's message from `text`: the order's cancel view
     answers a refusal with it.
   - An email to the customer about its shipment is declared in its own
     `emails.py` through `order.emails.shipment_email` (templates stay in
     `emails/order/`; `shipping_acs.emails`, `shipping_boxnow.emails`).
   - The routing and scheduling hooks, so no `core` or `tenant` module names
     the carrier. `api_urlconf` is its storefront API's URL conf, mounted
     under `api/v1/` after `shipping.urls` (which includes every carrier's).
     `webhook_urlconf` is the URL conf of the endpoints the courier calls,
     mounted at the root of both hosts by `shipping.webhook_urls`, with no
     language prefix (BoxNow: `shipping_boxnow.webhook_urls`). Its beat
     fan-outs (`run_for_all_tenants`) live in its own `tasks.py`; the ones
     that moved there from `tenant.tasks` keep their `tenant.tasks.fanout_*`
     names, which beat and queued messages address.
   - `create_demo_shipment(order, row, ...)` writes the shipment a demo
     account's order shipped under (`shipping.demo.ShipmentRow`, timed by
     `shipping.demo.event_times`), from the carrier's own `demo.py`; the demo
     seed (`devtools.demo_account`) asks the order's carrier for it.
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
7. Join the project-wide registries:
   - settings: the app in `TENANT_APPS` (`src/settings/application.py`), its
     beat entries (`src/settings/schedule.py`), throttle scopes
     (`src/settings/api.py`), platform integration settings
     (`src/settings/integrations.py`) and per-store settings
     (`src/settings/store_settings.py`), where it has any;
   - every model in the anonymise rules (`src/devtools/anonymise/rules.py`),
     which a test enforces;
   - its credential fields on `Tenant`, read by a reader in
     `src/tenant/credentials.py` and listed in `CARRIER_CREDENTIAL_FIELDS`;
   - any token it caches in `PROTECTED_FRAGMENTS`
     (`src/core/cache/protected.py`), so no purge forces a new login;
   - every model that decides where it delivers in the `invalidated_by` of
     `src/shipping/cache_surfaces.py` and `src/country/cache_surfaces.py`
     (ACS: `AcsStation`).

### 5.4 Operational alert emails (`src/shipping/alerts.py`)

Merchant-operations mail is a set of email types (`ops.<kind>`, declared in
`src/shipping/emails.py`, `src/shipping_acs/emails.py`, `src/order/emails.py`,
`src/giftcard/emails.py` and `src/product/emails.py`; see `docs/emails/types.md`,
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
- **One switch per type**, an `EmailSwitch` row (`docs/emails/sending.md`, The
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
