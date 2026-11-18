# Order system: creation paths

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 3. Order creation paths

Two entry points in `src/order/services.py` (the legacy
`OrderService.create_order`
— dead code with zero production callers — was removed 2026-08-24):

### 3.0 One total: the checkout quote and the expected total

`OrderQuoteService.quote` (`src/order/checkout/quote.py`) is the one
computation of what checkout charges. A rule added there reaches every
caller:

| Caller | Locks | Reads |
|---|---|---|
| `POST /api/v1/cart/quote` (`CartViewSet.quote`) | none | the whole quote, partial input allowed |
| `POST /api/v1/cart/create-payment-intent` | none | `amount_due` is the intent's amount |
| `create_order_from_cart` (§3.1) | `lock=True` | compares the intent's amount with `amount_due` |
| `create_order_from_cart_offline` (§3.2) | `lock=True` | the order's charges |

- **The rows** (`CheckoutQuote`): the products (`items_subtotal`), the
  promotions (`promotion`, `discount_total`), `free_threshold_value`
  ([§5.0](shipping.md)), the delivery (`shipping`: price and `waived_by`,
  a `ShippingCharge`), the pay-way fee (`payment_fee`: amount and
  `waived`), the loyalty discount, the gift-card plan, `grand_total`
  (products after promotions, plus shipping and the fee) and `amount_due`
  (what is left after loyalty and the gift cards: what the order stores).
  The parcel it weighs holds the promotion's gifts, and promotions are
  evaluated with the checkout email.
- **A row is never guessed.** Without a delivery `shipping` is `None`,
  without a pay way `payment_fee` is, and the rows that need both
  (`grand_total`, the gift-card plan, `amount_due`) are `None` with them.
  The callers that pass both read `quote.charges()`.
- **`lock=True` is order creation.** The candidate `Promotion` rows, the
  customer's row (when points are redeemed) and the gift cards are locked
  until commit, so what was quoted is what `PromotionEngine.record`,
  `LoyaltyService.redeem_points` and `GiftCardService.record_plan` write.
  A refused attached coupon raises `InvalidCouponError`; without the lock
  it is reported on the quote (`blocking_coupon`).
- **The order is built from the quote's rows**
  (`OrderService._quote_under_lock`): `shipping_price`,
  `payment_method_fee`, the promotion result, the points and the gift-card
  plan. `Order.calculate_order_total_amount()` still decides what is
  stored, and `_require_quoted_total` refuses (rolls back) an order whose
  own sum is not the quote's `amount_due`: a charge added to one and not
  the other, or a cart line changed between the quote and the line lock,
  fails the creation instead of producing an order no payment matches.
- **The expected total.** `POST /api/v1/order` takes an optional
  `expectedTotal` `{amount, currency}`: the amount due in minor units
  (cents). Both creation paths compare it, exactly, with the quote computed
  under the cart lock. A difference creates nothing and answers **409**
  `{detail, error: {type, quote}, code: "total_changed"}`, `quote` being
  the quote the order would have been created at, so the client shows the
  new total and sends its `expectedTotal`. This is the only amount check
  the order-first path has (cash on delivery, Viva).
  - What can change between the last render and the order: a price, VAT
    or markdown edit (a cart stores no price), a promotion starting, ending
    or reaching a usage limit, an edit to a shipping rate or a pay way, an
    ACS live price leaving its cache, another email than the one quoted
    with. Each is a `total_changed` refusal, by design.
  - **`error.type` is `invalid_order_data` for now.**
    `OrderCreateErrorType` is an enum in the API contract, and a new
    response enum value ships consumer first (`api-contract/README.md`):
    the storefront in production must list `total_changed` before Django
    may send it. Until then clients branch on `code` and the 409
    (`order.enum.create_error.TOTAL_CHANGED_CODE`); the gateway reads
    `code` already.
  - **Optional for now.** The storefront and the gateway start sending it,
    then a later release requires it (#635).
- **`POST /api/v1/cart/quote`** takes the choices of the payment-intent
  body, each optional (`CartQuoteRequestSerializer`). A delivery is priced
  once `shippingKind` and `countryId` are both given (order creation
  prices a missing kind as home delivery; a quote does not assume one).
  Besides the rows it returns `expectedTotal`, to send back with the
  order, and the priced lists: `shippingOptions` (every delivery the store
  offers to the country, `ShippingService.available_options` priced with
  the region and a free-shipping promotion, each with `waivedBy`; null
  without a country) and `payWays` (every pay way a shopper is offered
  for the chosen delivery, `PayWayService.shopper_facing` and
  `filter_for_shipping`, each with its fee and `waived`).
  - The cart is found as order creation finds it (`get_existing_cart`):
    404 `cart_not_found`, never a new cart; an empty one is 400
    `cart_empty`.
  - Refusals are `{detail, reason, code}`. A delivery that cannot be
    priced has a code of its own (`shipping.enum.ShippingRefusal`:
    `shipping_unavailable`, `shipping_parcel_too_large`,
    `shipping_weight_exceeded`), so a client drops the selection instead
    of matching translated text; the payment intent and order creation
    keep `invalid_order_data` with `fieldErrors` for the same three
    (`ShippingRefusedError` is an `InvalidOrderDataError`). The rest are
    the payment-intent endpoint's: 400 `pay_way_unavailable` / 404
    `pay_way_not_found`, 400 `loyalty_requires_authentication` /
    `loyalty_redemption_invalid` / `loyalty_redemption_too_small` (points
    worth less than a cent would be spent for a discount of 0.00; the
    detail says how many make one; order creation answers it as
    `invalid_order_data` with the message under
    `fieldErrors.loyaltyPointsToRedeem`), and the gift card's own reason.
  - `X-Cart-Token` is accepted (`docs/cart-agent-token.md`): the quote is
    then the cart owner's price. Order creation takes no token, so that
    quote must not feed the expected total of an anonymous order. Points
    are priced only for the signed-in owner: with a token,
    `loyaltyPointsToRedeem` is refused (`loyalty_requires_authentication`),
    because the refusal of too many points states the balance.
  - **A budget of its own.** The quote is repeated on every change of a
    checkout and polled by the agent gateway, so it is throttled by
    `cart_quote` / `cart_quote_anon` (`CartQuoteThrottle`,
    `CartQuoteAnonThrottle`: per cart for the gateway), not by the cart's
    write budget.
  - **Gift-card codes spend the balance check's budget.** The quote, the
    payment intent and order creation each say what a card settles (or
    that the code is no card), so a request to any of them that carries
    `giftCardCodes` also spends `gift_card_check`
    (`core.api.throttling.gift_card_code_throttles`, the one helper the
    three views add to their throttles): none is a faster balance oracle
    than `/giftcard/check`. A request without codes is not counted.
  - A polled quote does not reach the carrier: ACS's live price is cached
    for five minutes per country, region and weight bracket
    (`AcsCarrier._fetch_live_quote`), the entry order creation reads. A
    failed call is not cached; the stored rate is charged.
  - **A quote only prices.** Stock, the cart's validity, the wholesale
    minimum, the legal texts and the unpaid-order limit are order
    creation's to judge.
  - Not in the quote yet: the VAT total. The receipt's VAT
    (`order.accounting.taxation.vat_lines`) taxes shipping and the fee at
    the goods' rates and spreads the loyalty discount over the lines; the
    cart's `totalVatValue` is the goods' VAT only.

### 3.1 `create_order_from_cart` — payment-first / online
- **Caller**: `OrderViewSet.create` for an intent-based ONLINE pay way (Stripe)
  whose request carries `payment_intent_id`. Every client reaches it through
  `POST /api/v1/order`: the storefront, and the agent gateway's UCP/ACP
  checkout (which sends no intent, so it takes §3.2). Nothing else calls it.
- Sets `status=PENDING`, `payment_status=<provider-returned>`.
- **Does NOT** dispatch courier task here. Online payments defer
  the dispatch to the payment-success webhook (PR #1 commit
  `59527a87`; the `transaction.on_commit` invariant in
  [§8](invariants.md) has the reason).
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
  currency against the total it computes under lock (Step 2.5, the quote
  of §3.0, which also prices loyalty and gift cards under their own
  locks). Order of checks
  therefore changed: a cart that cannot be checked out costs one provider
  read first, with no lock.
- **The intent must be this cart's** (`src/order/payments/payment_binding.py`,
  `OrderService._verify_intent_binding`). An intent proves that money moved,
  not what for, so each intent is tied to its purpose. Every PaymentIntent we
  create now carries server-set metadata
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
  `reserve_cart_stock` takes the same locks itself, through the same helper.
  Not covered: an admin edit that changes the quantity of several
  `OrderItem` rows in one request (`handle_order_item_post_save` →
  `StockManager.adjust_stock`) locks their products in form order.
- **A cart's stock holds are replaced as a whole, never stacked**
  (`POST cart/reserve-stock` -> `StockManager.reserve_cart_stock`). A cart
  (session = the cart UUID) owns one set of `StockReservation` rows. One call
  locks the products of the old and the new set (`lock_products`, pk order),
  releases every unconsumed hold of the session (expired ones too) through
  `release_reservation` (`consumed=True`, a `StockLog` RELEASE line each, no
  order), then reserves the new quantities, all in one transaction. The call
  first locks the `Cart` row, the lock checkout takes first, so the order is
  cart, then products, the same as checkout; `reserve_cart_stock`, a checkout
  of that cart and the login merge that rehomes its holds
  (`CartService.merge_carts` locks both cart rows, lowest pk first) run one at
  a time. A checkout therefore never reads a hold set a re-reserve is half way
  through replacing. A reload,
  a retry, an agent completing twice, a changed quantity or a removed line
  therefore never leave the cart blocked by its own earlier holds
  (`stock == quantity` used to answer 409 on the second attempt and keep the
  units held for the TTL) and never leave a hold on a product the cart no
  longer has. Other sessions' holds still count, so two carts still exclude
  each other for the last unit. If any line is short the whole call rolls
  back (`CartStockUnavailableError`, one entry per short line, answered as 409
  `insufficient_stock` with `failed_items`) and the cart keeps the holds it
  had. `StockManager.reserve_stock` stays the per-product primitive and counts
  every active hold, so only reserve through `reserve_cart_stock`. Order
  creation looks reservations up by SESSION, not by id
  (`session_id=cart.uuid, consumed=False`), so the ids an older response
  returned are irrelevant: they are already consumed, and
  `release-reservations` treats a consumed id as a no-op.

  **Setting an absolute stock (`StockManager.set_stock`).** A writer that knows
  the number it wants rather than a delta (the catalogue import) calls
  `set_stock(product_id, quantity, reason_code=StockLogReason.CATALOGUE_IMPORT,
  reason=..., performed_by=...)`. It locks the product row, saves the new stock
  under the `"StockManager: set_stock"` change reason (so the stock-save signal
  in
  `src/product/signals.py`, which writes a `MANUAL_ADJUSTMENT` row for any
  stock save
  not made by a `StockManager` method, writes nothing) and writes exactly ONE
  `StockLog` row with the reason code, the before and after values and the
  staff
  member. A stock that already equals `quantity` writes nothing at all (no
  save,
  no history row, no log, no reindex) and the call returns `False`. A negative
  quantity raises `ValueError`. Like the other `StockManager` writers it does
  not
  look at reservations: the import's preview warns when the new stock is below
  what is reserved.

### 3.2 `create_order_from_cart_offline` — order-first / COD / Viva
- **Caller**: Nuxt checkout for offline pay-ways + Viva redirect flow, and the
  agent gateway's UCP/ACP checkout.
- Sets `status=PENDING`, `payment_status=PENDING`.
- Same lock order as §3.1: the cart row, then what the quote locks (§3.0:
  promotions, the customer's row when points are redeemed, the gift
  cards), then the cart lines, then the products in pk order before any
  `OrderItem` insert. Until #635 this path priced loyalty and the gift
  cards after the lines were written; it now takes those locks where §3.1
  always did. There is no provider call here.
- **Not idempotent.** A hosted-payment order (Viva) keeps the cart, so a
  repeat submit creates a second PENDING order that takes its own stock until
  its payment hold ends ([§7.1.0](cancel.md)). Reusing the open order needs a
  key over
  the whole request (cart lines and prices, pay way, address, loyalty points,
  gift-card codes) and has to run before BOTH stock validations (the view's and
  the service's: the open order already took the stock), and what the first
  creation consumed (the coupon, the gift-card balance, the loyalty points) is
  not recoverable from the inputs. It is a separate change, not a lookup.
- **An agent order is refused while its cart's earlier order awaits its
  online payment** (`OrderService._refuse_agent_order_on_unpaid_cart`, #692).
  The repeat submit above is a person's own act on the storefront ("Back,
  then order again") and stays as it is. Through the agent gateway it is a
  platform that lost its checkout or sent it twice: a second order for the
  same goods, with its own stock decrement, that the buyer never asked for.
  - **Which new order.** One the gateway places: `AttributionInput.from_agent`,
    an `agentProtocol` believed beside the gateway's secret
    ([§3.4](creation-records.md)), the signal that books the order "AI
    agent". Any other order is not looked at. Its own pay way does not
    matter: cash on delivery is refused too.
  - **Which earlier order.** Any order of this cart
    (`metadata["cart_snapshot"]["cart_uuid"]`, a `@>` lookup that
    `order_meta_ix` serves) in `OrderQuerySet.awaiting_online_payment()`: not
    deleted, an ONLINE pay way, a payment that is not settled. A payment
    that FAILED or is PROCESSING is still awaited: a failed attempt does not
    end the payment ([§7.1.0](cancel.md)). The earlier order need not be an
    agent's. It stops counting when it is paid, canceled (by its buyer, staff
    or the sweep at the end of its hold) or deleted.
  - **Where.** In `create_order_from_cart_offline` alone, under the cart row
    lock and before the cart is validated. The lock is what serialises two
    agent orders racing for one cart: the second waits for it and then finds
    the first one's committed order.
  - **Not on the payment-first path (§3.1).** There the payment is already
    taken when the order is created, so a refusal would keep money for no
    order, and the order it creates is a paid one, not a second order
    awaiting its payment; the earlier unpaid order is left to the sweep. The
    gateway sends no payment intent, so no agent order takes that path
    today.
  - **The answer.** `CartHasUnpaidOrderError`, which `OrderViewSet.create`
    answers as it answers `cart_empty`: `400`, `code`
    `cart_has_unpaid_order`, the message under `cart`. It names no order: an
    order's uuid opens a guest order, and the caller may hold only the
    cart's id.
  - **Limits.** The view checks the cart's stock before the service runs, so
    when the earlier order took the last units the second call is answered
    `insufficient_stock`; no second order either way.
- For **COD/offline**: dispatches courier task via
  `_dispatch_shipment_creation_task` immediately. The
  dispatch is wrapped in `transaction.on_commit` inside
  `ShippingService.dispatch_create_shipment_task` so the worker
  only sees the order after the create transaction commits.
- For **online (Viva)**: skips the immediate dispatch — Viva's
  webhook handler does it after payment confirms.

### 3.3 Delivery-address rules (`src/core/validators/address.py`)

Every address write is checked against the order's country row before
an order or saved address exists, so a courier never receives an
address it cannot place (prod order #316: street "1", street number
"70300", a non-numeric postcode, rejected by `ACS_Create_Voucher`).

- **Postcode**: normalised (trimmed, upper-case, whitespace collapsed to
  one space) and full-matched (ASCII) against
  `Country.postal_code_pattern`. The pattern and `postal_code_example`
  are Google's Address Data Service `zip` / first `zipex`, seeded by
  `src/country/migrations/0011_country_postal_code_format.py` and editable
  in the Country admin. A blank pattern means only "required".
- **Street** must contain a letter; **street number** must not itself
  match the country's postcode pattern (4+ characters).
- **Region**: required exactly when the country has any (`Country.
  has_regions`, an `Exists(Region…)` annotation `for_list()`/
  `for_detail()` add — never a prefetch, so the storefront's country
  picker can decide whether to show the region field per country
  without an extra query); when given, it must belong to the selected
  country. `src/country/migrations/0012_seed_iso_countries.py` seeds the
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
  (`src/tenant/reference_guard.py`): the admin lists the stores on its
  confirmation page (`tenant.deletion.PublicReferenceDataDeletionMixin`)
  and a `pre_delete` receiver in `src/tenant/signals.py` refuses every other
  path. A country is also refused when a store uses one of its regions,
  because the regions go with it. Soft-deleted orders count (the foreign
  key is still there); simple-history rows do not. Deleting a row nobody
  uses works from the platform admin. Both ends of a store's use are
  found from the models' own reverse relations, hidden ones included, so
  a new model with a foreign key to `Country` is covered without a list
  to update.
