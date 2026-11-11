# Order system: creation paths

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 3. Order creation paths

Two entry points in `src/order/services.py` (the legacy
`OrderService.create_order`
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
- **The intent must be this cart's** (`src/order/payment_binding.py`,
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
- Same lock order as §3.1 (cart row, then promotions, then products in pk
  order before any `OrderItem` insert). There is no provider call here.
- **Not idempotent.** A hosted-payment order (Viva) keeps the cart, so a
  repeat submit creates a second PENDING order that takes its own stock until
  its payment hold ends ([§7.1.0](cancel.md)). Reusing the open order needs a
  key over
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
