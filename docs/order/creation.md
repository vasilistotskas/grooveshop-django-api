# Order system: creation paths

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 3. Order creation paths

One entry point in `src/order/services.py`,
`OrderService.create_order_from_cart` (§3.1), for every pay way. Every
order is created before it is paid, so no money moves before the locked
quote has run every refusal.

### 3.0 One total: the checkout quote and the expected total

`OrderQuoteService.quote` (`src/order/checkout/quote.py`) is the one
computation of what checkout charges. A rule added there reaches every
caller:

| Caller | Locks | Reads |
|---|---|---|
| `POST /api/v1/cart/quote` (`CartViewSet.quote`) | none | the whole quote, partial input allowed |
| `create_order_from_cart` (§3.1) | `lock=True` | the order's charges |

- **The lines** (`CheckoutQuote.lines`, `QuoteLine`): the cart's lines
  as priced (`OrderQuoteService.priced_lines`): product, quantity, unit
  price (wholesale prices bound) and the promotion money the line carries
  (`promotion.line_discounts`). The quote reads the lines once
  (`Cart.get_items`) and hands that read to `PromotionEngine.evaluate`
  (`items=`), so the promotions, the subtotal, the parcel and the cart
  digest all come from it: the lines a quote returns are the ones its
  total and digest cover. The digest's canonical form is pinned by
  `tests/unit/order/test_cart_digest_golden.py`.
- **The rows** (`CheckoutQuote`): the products (`items_subtotal`, the
  lines' sum), the promotions (`promotion`, `discount_total`, and
  `coupons`: `CartDiscountResult.coupon_verdicts` on each attached code,
  the verdict the cart's `appliedCoupons` gives too, on the codes the
  evaluation itself read, `CartDiscountResult.codes`, never a second read), `free_threshold_value`
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
  until commit, so what was quoted is what `promotion.engine.redemptions.record_redemptions`,
  `LoyaltyService.redeem_points` and `GiftCardService.record_plan` write.
  A refused attached coupon raises `InvalidCouponError`; without the lock
  it is reported on the quote (`blocking_coupon`).
- **No carrier is asked while those rows are locked.** A carrier's live
  price is an HTTP call that can take seconds (ACS: a 15 s timeout), and
  every checkout sharing the cart, a promotion, the customer or a gift
  card would wait behind it. So order creation resolves it BEFORE
  its transaction (`OrderService._resolve_live_shipping` ->
  `OrderQuoteService.resolve_live_shipping` ->
  `ShippingService.resolve_live_price`), where the carrier's adapter
  caches the answer, and the locked quote prices with `cached_only`: it
  reads that cache and never calls out. When the cache holds no answer
  for the parcel the locked quote weighs (the cart changed in between,
  or the entry expired) the creation rolls back
  (`ShippingPriceUnresolvedError`), the cart is priced again with
  nothing locked, and the answer is 409 `total_changed` with that quote
  (`OrderService._refuse_unpriced`): the client orders again, against a
  resolved price.
- **Points pay for goods, never for shipping or a fee.** The most a
  redemption takes off is what the goods cost after the promotions
  (`free_threshold_value`): the quote prices it under that cap and
  `redeem_points` is given the same one, as is `POST /loyalty/redeem` on
  an existing order.
- **A pay way is accepted exactly when it is listed.** The quote's
  `payWays`, the pay way a quote accepts, and the one order creation
  accepts (`OrderViewSet._validate_pay_way_for_order`) all come from
  `OrderQuoteService.offered_pay_ways`: active, through a provider the
  store holds credentials for, and not excluded for the delivery (a home
  delivery that names no carrier is judged by every carrier that could
  take it). The refusal is 400 `pay_way_unavailable` on each; order
  creation puts its message under `payWayId`. An order sent without a
  `shippingKind` is a home delivery for the price, the carrier it is
  assigned and this rule alike: all three read
  `OrderQuoteService.delivery_kind`.
- **The order is built from the quote's rows**
  (`OrderService._quote_under_lock`): `shipping_price`,
  `payment_method_fee`, the promotion result, the points and the gift-card
  plan. `Order.calculate_order_total_amount()` still decides what is
  stored, and `_require_quoted_total` refuses (rolls back) an order whose
  own sum is not the quote's `amount_due`: a charge added to one and not
  the other, or a cart line changed between the quote and the line lock,
  fails the creation instead of producing an order no payment matches.
- **The expected total.** `POST /api/v1/order` requires
  `expectedTotal` `{amount, currency}`: the amount due in minor units
  (cents), as the quote returned it. An order is never created at a total
  the client did not send. Order creation compares it, exactly, with the
  quote computed under the cart lock. A difference creates nothing and answers **409**
  `{detail, error: {type, quote}, code: "total_changed"}`, `quote` being
  the quote the order would have been created at, so the client shows the
  new total and sends its `expectedTotal`. This is the only amount check
  creation has: the payment is opened afterwards for the order's own
  `calculate_order_total_amount()`.
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
  - **Required since #635's third release**, once the storefront
    (v3.345.3) and the gateway (v1.49.0) in production sent it on every
    order. Nothing creates an order without the total check.
- **The cart digest.** A quote with a delivery chosen also returns
  `cartDigest` (`OrderQuoteService.cart_digest`): an HMAC, keyed by the
  `SECRET_KEY`, over a canonical form of what the order would be made of:
  each of the quote's `lines` (product, quantity, unit price as priced,
  the promotion money it carries), the promotions that apply with their codes, amounts and
  gifts, the delivery (kind as `delivery_kind` reads it, the carrier that
  takes it, as `_carrier` resolves one a home delivery does not name,
  country, region, the pickup point). It is a check of contents: it holds
  no pay way (a UCP checkout may name its payment method only when it
  completes) and no shipping charge, fee, loyalty or gift-card amount;
  `expectedTotal` holds what is paid, the pay way's fee included. It is stable while those do not change and reveals
  nothing of the cart. `POST /api/v1/order` takes an optional
  `expectedCartDigest`; order creation compares it under the cart
  lock, before the total, and a difference creates nothing and answers
  **409** `{detail, error: {type, quote}, code: "cart_changed"}`. It
  catches what the total cannot: a line swapped for one at the same
  price, quantities that trade off, another locker at the same rate.
  - **The quote takes what the order takes.** The pickup point
    (`boxnowLockerId`, `acsStationExternalId`, `acsStationBranch`,
    `order.serializers.quote.PickupPointSerializer`) is a field of both
    bodies, and both build the delivery with `ShippingChoice.of`, which
    keeps only the chosen carrier's `location_keys`
    (`shipping.interfaces.ShippingCarrierInterface`). Every order-creation
    field is placed as digested, priced or not priced in
    `tests/integration/order/test_cart_digest.py`, so a field added to one
    body and not the other fails there.
  - **A code, not an enum value.** As `total_changed`, `cart_changed` is
    sent under `error.type` `invalid_order_data`
    (`order.enum.create_error.CART_CHANGED_CODE`), and only to a request
    carrying `expectedCartDigest`, which is optional.
- **`POST /api/v1/cart/quote`** takes the checkout's choices, each
  optional (`CartQuoteRequestSerializer`). A delivery is priced
  once `shippingKind` and `countryId` are both given (order creation
  prices a missing kind as home delivery; a quote does not assume one).
  Besides the rows it returns `expectedTotal` and `cartDigest`, to send
  back with the order; `lines` (`QuoteLineSerializer`: the cart item
  `id`, `productId`, `productName` in the request's language,
  `mainImagePath`, `quantity`, `unitPrice`, `totalPrice` before
  promotions and `promotionDiscount`, which sum to `itemsSubtotal` and
  `discountTotal`) and `appliedCoupons` (each attached code's `amount`,
  `freeShipping`, `gift` or `rejection`: the cart's `appliedCoupons`, one
  serializer and one schema declaration for both,
  `order.serializers.quote.AppliedCouponSerializer` and
  `APPLIED_COUPONS_SCHEMA`, inline because drf-spectacular would lift a
  component's enum into a `oneOf` the live consumers read as a type
  change), so a client renders the
  checkout from the one answer its total came from (the agent gateway
  reads no cart beside it); and the priced lists: `shippingOptions` (every delivery the store
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
    of matching translated text; order creation keeps
    `invalid_order_data` with `fieldErrors` for the same three
    (`ShippingRefusedError` is an `InvalidOrderDataError`). The rest: 400
    `pay_way_unavailable` / 404
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
    `cart_quote` (`CartQuoteThrottle`, signed-in callers only) or
    `cart_quote_anon` (`CartQuoteAnonThrottle`: guests, per cart for the
    gateway), not by the cart's write budget.
  - **Guessing a gift-card code is budgeted, using one is not.** The
    quote and order creation each say what a card settles (or that the
    code is no card), so each could be used to guess codes.
    `giftcard.throttles.gift_card_code_throttles`, the one helper the two
    views add to their throttles, gives a request that carries
    `giftCardCodes` a `GiftCardCodeThrottle`:
    - only a code **new to the cart** counts. The cart keeps the codes it
      has been allowed to try for `GIFT_CARD_CODES_SEEN_TTL_SECONDS`
      (keyed digests, never the code), across the two routes, so a
      checkout that re-prices the same card on every change is never
      refused; a request with several new codes costs that many;
    - the caller pays, from the `gift_card_check` budget that
      `/giftcard/check` spends too (6 a minute for all of a caller's
      guesses): the signed-in customer or the proven address, and the
      cart for a guest request the agent gateway makes, since every buyer
      of a platform arrives from its address;
    - the check and the count are one step per caller, made while the
      budget's key is held (`CustomCache.lock`), so guesses that arrive
      together are counted one after the other; `/giftcard/check` counts
      the same way;
    - a request this budget refuses records nothing, so its codes stay
      new. (One refused by another throttle of the view may already have
      been counted and recorded: each code still counts once.)
    - the codes are read by the request serializer's own
      `gift_card_codes` field (`get_value`, `run_validation`), so a JSON
      list, a repeated key of a form or multipart body and indexed form
      keys are counted exactly as they are priced.
  - A polled quote does not reach the carrier each time: ACS's live price
    is cached per country, region and weight bracket
    (`AcsCarrier._fetch_live_quote`), a price for five minutes and a
    failed call for one (the stored rate is charged meanwhile).
  - **A quote only prices.** Stock, the cart's validity, the wholesale
    minimum, the legal texts and the unpaid-order limit are order
    creation's to judge.
  - Not in the quote yet: the VAT total. The receipt's VAT
    (`order.accounting.taxation.vat_lines`) taxes shipping and the fee at
    the goods' rates and spreads the loyalty discount over the lines; the
    cart's `totalVatValue` is the goods' VAT only.

### 3.1 `create_order_from_cart` — every pay way, order first
- **Caller**: `OrderViewSet.create` (`POST /api/v1/order`) alone, for every
  pay way: the storefront's checkout (both designs) and the agent gateway's
  UCP/ACP checkout. The body carries no payment; the cart comes from
  `X-Cart-Id`.
- Sets `status=PENDING`, `payment_status=PENDING`.
- **Every online pay way is order-first.** An order for Stripe or
  Viva is created PENDING with nothing charged and no `payment_id`, the cart
  kept (`Order.awaits_online_payment`; it clears on `order_paid`), the
  courier voucher deferred to the payment, and the payment opened
  afterwards on the provider's hosted page by `create_checkout_session`
  (or, for an agent's delegated card, `confirm_agent_payment`), which
  charge `calculate_order_total_amount()`, what is left after the gift
  cards, points and promotions. The quote's locked refusals (stock, total,
  cart digest, the wholesale minimum) therefore all run before any payment
  exists. Deductions covering the whole total settle the order here
  (step 6.8: `GIFTCARD_<uuid>` or `DISCOUNT_<uuid>`, the voucher
  dispatched, the cart cleared), whatever the pay way. There is no refusal
  of a partial cover: the remainder is what the payment is opened for.
- **No provider call, and no carrier call inside the transaction.** The
  carrier's live price is resolved before the transaction opens (§3.0),
  and `OrderViewSet.create` is deliberately NOT `@transaction.atomic`: a
  view-level block would put that HTTP call back inside a transaction,
  idle on a connection that `idle_in_transaction_session_timeout` kills.
  `_create_order_locked` is the one transaction.
- **Lock order**: the cart row, then what the quote locks (§3.0:
  promotions, the customer's row when points are redeemed, the gift
  cards), then the cart lines, then the products in pk order before any
  `OrderItem` insert (below).
- **Product locks are taken in ascending pk order, all in one statement, before
  the first row that references a product is inserted.** The cart-item query
  locks only the cart lines (`of=("self",)`, not the joined products), then
  `OrderService._lock_order_products` → `StockManager.lock_products` locks
  every product the order touches (cart lines, the cart session's reservations,
  free-gift products) once, `ORDER BY` pk, so two carts holding the same
  products in different line orders never take them in opposite orders, which
  PostgreSQL would resolve by aborting one checkout. It must precede the
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
  therefore never leave the cart blocked by its own earlier holds (with
  `stock == quantity`, a second attempt would otherwise answer 409 and keep
  the units held for the TTL) and never leave a hold on a product the cart no
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

### 3.2 Repeats: the storefront's, and one agent checkout, one order
- **The storefront names no checkout.** A hosted-payment order (Viva, Stripe)
  keeps the cart, so the same cart can be submitted again: the same checkout
  resumes its order (below), a changed one places a new order that takes its
  own stock until its payment hold ends ([§7.1.0](cancel.md)). The agent
  gateway names its checkout instead, and is answered by it (next).
- **One agent checkout, one order** (`Order.agent_checkout`, #637). The
  agent gateway sends its checkout session's id as `agentCheckout` and,
  when an order call goes unanswered, asks again with the same id. A
  repeat is answered with the order that checkout placed, the same `201`
  and body the first call got, and creates, refuses and sends nothing.
  - **Only the gateway names one.** `agentCheckout` is believed beside the
    gateway's secret in `X-Internal-Gateway` alone
    (`OrderViewSet._named_agent_checkout`), as `agentProtocol` is
    ([§3.4](creation-records.md)). From any other caller it is refused,
    a malformed value included, before anything is looked up or created
    (`OrderViewSet._refuse_agent_checkout`: 400 with `code`
    `agent_checkout_not_allowed` under `fields.agentCheckout`). Refused,
    not dropped: a gateway whose secret Django does not hold would
    otherwise have its checkout ignored, and every repeat of an order
    whose answer it lost would place another. The storefront never sends
    the field.
  - **First, before anything is judged, the body's validation included.**
    `OrderViewSet.create` reads the bare id from the body (through the
    request serializer's own `agent_checkout` field; a malformed value
    names no checkout and the validation refuses it) and looks the
    checkout up (`OrderService.order_for_agent_checkout`) before the body
    is validated. The first order took the stock and kept (online), emptied
    or deleted (cash on delivery) the cart, and validation reads rows that
    can change after it (an active carrier, a country, a region), so the
    validation and the pay way, cart, stock and unpaid-cart checks could
    each refuse the repeat of an order that exists.
  - **No live stock hold is needed.** Creation reconciles the cart's holds
    under the product locks (`_consume_stock_for_order`): a missing or
    expired hold is released and the cart's quantity is taken from the
    stock that other carts' active holds leave. A call whose first attempt
    never reached Django is an ordinary creation, refused only when that
    stock no longer covers the cart.
  - **Under the cart lock.** The locked half (`_create_order_locked`)
    looks it up again right after the cart row lock and before `_refuse_agent_order_on_unpaid_cart`.
    Two calls of one checkout on one cart serialise there and the second
    returns the first's committed order.
  - **The index.** `order_agent_checkout_unique` is a partial unique index
    over the non-NULL ids (orders not placed through the gateway carry
    NULL). Two calls on two different carts can both miss the lookup; the
    second insert is then rejected, read off the error by its constraint
    name and raised as `AgentCheckoutOrderedError`, so it rolls back
    whatever it took.
  - **A refusal after a racing commit.** When a call is refused after the
    first lookup (the body's validation or the pay way, which read rows
    that changed meanwhile, the cart the racing call deleted, its stock,
    the index), the view looks again and answers the order if the checkout
    has one now; otherwise the refusal stands.
  - Deleted orders count: the checkout placed it, and deleting it does not
    let the checkout place another. The response carries `agentCheckout`
    (`OrderDetailSerializer`; null for any other order), and so does the
    gateway's internal read of an order.
- **An agent order is refused while its cart's earlier order awaits its
  online payment** (`OrderService._refuse_agent_order_on_unpaid_cart`, #692).
  The repeat submit above is a person's own act on the storefront ("Back,
  then order again"). Through the agent gateway it is a
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
  - **Where.** In `create_order_from_cart` alone, under the cart row
    lock and before the cart is validated. The lock is what serialises two
    agent orders racing for one cart: the second waits for it and then finds
    the first one's committed order.
  - **The answer.** `CartHasUnpaidOrderError`, which `OrderViewSet.create`
    answers as it answers `cart_empty`: `400`, `code`
    `cart_has_unpaid_order`, the message under `cart`. It names no order: an
    order's uuid opens a guest order, and the caller may hold only the
    cart's id.
  - **Before the stock.** The cart is validated by the service alone, after
    this refusal: when the earlier order took the last units, the agent is
    still told `cart_has_unpaid_order`, not `insufficient_stock`.
- **A repeated checkout resumes its cart's unpaid order** (#694,
  `order.checkout.resume`). The storefront's "Back, then order again" with
  nothing changed would otherwise place a second unpaid online order holding
  the same stock, with its own payment link.
  - **The same checkout.** `checkout_fingerprint`, an HMAC of what the
    shopper chose: the quote's `cart_digest` (lines, promotions, delivery),
    `amount_due`, the pay way, the loyalty points and gift cards redeemed, and
    every field of the shipping address. An online order stores it as
    `metadata["checkout_fingerprint"]`, with an HMAC of its own row's address
    beside it (`address_fingerprint`), so an order edited since (its owner, or
    staff) is not the same. Both are personal (`Order.PERSONAL_METADATA`).
  - **The order resumed.** `order_to_resume`: the one this checkout made, the
    latest if there are two, whichever was placed last, among
    `unpaid_orders_of_cart` (they hold their stock for an online payment, and
    are not an agent's: the gateway's are answered by their agent checkout or
    refused, above), not canceled, and with a payment window that still fits a
    link of its provider (`payment_hold.window_closed` with
    `min_hosted_session`). One whose payment is being decided (processing,
    flagged for another amount, or paid while the repeat waited) is returned
    whatever its window: asking for its link answers that a payment is in
    progress, where a second order would ask the shopper to pay twice. Its row
    is locked.
  - **When.** Under the cart row lock, before the cart is validated, since the
    order holds the cart's stock. The quote is priced only when the cart has
    such an order. A quote that refuses, another total or other contents are a
    changed checkout, answered by the creation that follows. Two identical
    checkouts racing end with one order.
  - **The stock.** The storefront reserves before every order
    (`StockManager.reserve_cart_stock`), which does not hold again the units
    the cart's unpaid order already took (`units_held_for_cart`), so a
    repeat of the last units is not refused there; a resume releases any hold
    left. The storefront still marks a line whose product has no stock left, so
    that shopper's way back is the order's own "Pay now".
  - **The answer.** The order, `201`, as for a repeat of an agent checkout,
    with the repeat's own ads consent and browser data, language and newly
    accepted legal texts. The storefront then asks for its payment link and
    gets the live one ([§4](payments.md)).
  - **A changed checkout** places a new order, as before. One that spent
    something is not the same the second time: a drained gift card is refused
    (`gift_card_not_redeemable`), a single-use coupon is gone from the cart,
    and points are there only with a balance that covers them twice. Cancelling the
    earlier order in the same step (supersede) waits for a verified way to
    close its payment link first.
- For **COD/offline**: dispatches courier task via
  `_dispatch_shipment_creation_task` immediately. The
  dispatch is wrapped in `transaction.on_commit` inside
  `ShippingService.dispatch_create_shipment_task` so the worker
  only sees the order after the create transaction commits.
- For **online (Viva, Stripe)**: skips the immediate dispatch — the
  provider's webhook handler does it after the payment confirms.
