# Order system: critical invariants

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 8. Critical invariants

These exist for a reason. Don't undo any without first re-reading
the linked section or the originating commit's message.

| Invariant | Anchor |
|---|---|
| No carrier mint while `Order.awaits_online_payment` (`ShipmentAwaitingPaymentError`), nor for an order that ships nothing (`Order.ships_nothing`, `ShipmentNotNeededError`) | [§5](shipping.md) |
| A payment-success handler checks `is_payment_after_cancel` BEFORE its settled-state guard | [§2.2](state-machines.md) |
| A second payment on an already paid order keeps the first `payment_id`, flags `Order.overcharged_at` and alerts ops once; nothing is refunded automatically | [§7.3](payment-ledger.md) |
| A placed order's lines (product, quantity, price) are read-only in every admin surface; the name and SKU on an invoice, email or API line are the line's own snapshot | [§3.7](creation-records.md) |
| Order `status` changes only through admin actions / `OrderLifecycleService`, never the change form; CANCELED only through `OrderLifecycleService.cancel_order`, never `update_order_status` or the `update_status` endpoint | [§2.1](state-machines.md), [§7.1](cancel.md) |
| A BoxNow `canceled` parcel event never changes the order (status, stock, payment): it records the shipment CANCELED, and an open order behind it is "follow up" until staff re-issue the parcel or cancel the order. Only ACS routes a canceled shipment to `cancel_for_dead_shipment` | [§2.1](state-machines.md), [§2.3](state-machines.md), [§7.1](cancel.md) (#600) |
| An order cancel never calls the courier inside its transaction: `_cancel_order_locked` commits the cancel with `metadata['shipment_cancel_pending']`, `cancel_attached_shipment` asks the courier after the commit (`transaction.on_commit`, so after the caller's own transaction too) and removes the flag with the answer, and `finish_pending_shipment_cancels` asks for a cancel whose worker stopped in between, and again for one whose courier gave no answer, until `ORDER_SHIPMENT_CANCEL_RETRY_HOURS` after the cancel | [§7.1.2](cancel.md) (#713) |
| An order row and its carrier shipment row are locked order first, through `shipping.locks.lock_with_order` alone; a joined `select_for_update().select_related("order")` locks them the other way round | [§5](shipping.md) (#613) |
| Operational mail to a store's operators is an `ops.*` email type sent through `send_email` only (the store's `EmailSwitch` is the one gate); an alert a store may switch off has a dashboard counter by the same rule as its email | [§5.4](shipping.md) |
| "Follow up" is computed (`OrderQuerySet.needing_follow_up`), never stored, and its unpaid-online exclusion is `awaiting_online_payment()` (settled statuses; `Order.awaits_online_payment` is the same rule for one row); its canceled-parcel part asks each carrier through `ShippingService.canceled_shipment_q`, never a carrier import in `order` | [§2.3](state-machines.md) |
| One order per provider payment: `Order.payment_id` is unique when non-empty (`order_payment_id_unique`). Order creation takes no payment: every order is created first, under the locked quote, and paid afterwards (#818) | [§3.1](creation.md) |
| One order per agent checkout: `Order.agent_checkout` is unique when set (`order_agent_checkout_unique`), it is believed only beside the gateway's secret, and order creation answers a repeat with that checkout's order (looked up before anything is judged, again under the cart lock, and after a refusal that a racing call caused), a unique-index race between two carts included | [§3.2](creation.md) |
| An order the agent gateway places (`AttributionInput.from_agent`) is refused, `cart_has_unpaid_order`, while an order of the same cart is in `OrderQuerySet.awaiting_online_payment()`: one helper, called under the cart row lock by `create_order_from_cart`. An order without a believed agent protocol is never checked, and the refusal names no order | [§3.2](creation.md) (#692) |
| No provider or carrier HTTP call inside the order-creation transaction (nor in a view-level `atomic`): order creation asks no provider at all, and the carrier's live price is resolved first, the locked quote reading it from the cache alone (`cached_only`), refusing as `total_changed` when it is not there | [§3.0](creation.md), [§3.1](creation.md) |
| Loyalty points pay for goods only: a redemption is capped at the goods after promotions (`CheckoutQuote.free_threshold_value`), on the quote, order creation and `/loyalty/redeem`; it never offsets shipping or the pay-way fee | [§3.0](creation.md) |
| Product rows are locked in ascending pk order, in one statement, before the first row that references a product is inserted (`StockManager.lock_products`); a cart-item lock never joins the products (`of=("self",)`) | [§3.1](creation.md) |
| Every checkout-created order since `0059_orderattribution` has exactly one `OrderAttribution` row (`direct` when there was no signal); for a checkout order a missing row means "older than attribution", never "direct"; `source_type=agent` only with the gateway's `X-Internal-Gateway` secret | [§3.4](creation-records.md) |
| A cart records its first touch (`CartAttribution`) only through `CartAttributionService.capture`, called before the line is written: an empty cart takes it, a cart holding lines keeps it, a merge keeps the earlier; order creation copies it to `OrderAttribution.cart_source*` because the cart is deleted with its lines | [§3.4](creation-records.md) |
| An issued tax document never re-reads the catalogue's VAT: invoices and myDATA split each line at `OrderItem.vat_rate`, snapshotted at creation, and tax shipping and the payment fee at the rate(s) of the goods (`src/order/accounting/taxation.py`); `Invoice` totals and the myDATA `invoiceSummary` come from the same lines | [§3.5](creation-records.md) |
| A MARK is final: every write of a myDATA send (`service.submit_document`: the intent, a refusal, an unanswered send) re-reads the document's row under its lock and leaves one that holds a MARK as it is, so overlapping senders (retry chain, sweep, SHIPPED, staff) can never demote it to SUBMITTED or REJECTED | [§7.4](fiscal-mydata.md) (#760) |
| A courier order's receipt is set aside for its cancel (`hold_receipt_for_cancel`) inside `_cancel_order_locked`, under the order lock and before the save that asks for credits; credits are created under the same lock, and the receipt email checks `Invoice.is_deliverable` when it runs | [§7.1](cancel.md), [§7.4](fiscal-movement-receipts.md) (#760) |
| The nightly demo reset returns the published demo gift card (`GIFT_CARD_CODE`) to its issued value with one `ADJUST` ledger row, so the ledger sum stays the balance and every guest spend stays on the card's history; only on `is_demo` tenants, and silently (no mail, no dispatch) | `src/devtools/demo_account.py: _restore_gift_card` |
| The checkout total is computed once, by `OrderQuoteService.quote`: `POST /cart/quote` and order creation (`lock=True`) read it. An order is built from the quote's rows and refused (`OrderService._require_quoted_total`) when its own sum is not the quote's `amount_due` | [§3.0](creation.md) (#635) |
| An order exists only at the `expectedTotal` its creation request carried (required on `POST /api/v1/order`): compared exactly (minor units and currency) with the quote computed under the cart lock, else 409 `code: total_changed` with the quote and nothing created. Its `error.type` stays `invalid_order_data` until the storefront in production lists `total_changed` | [§3.0](creation.md) (#635) |
| An order created with `expectedCartDigest` is made of exactly what that quote priced: the lines, the promotions and the delivery with its pickup point, not the pay way (`OrderQuoteService.cart_digest`), compared under the cart lock before the total, else 409 `code: cart_changed` with the quote and nothing created. It is a check of contents: it holds the line prices and their promotion money, not the shipping charge, the fee or the loyalty and gift-card amounts, which `expectedTotal` holds. The quote and order creation take the same pickup-point fields and build the delivery with `ShippingChoice.of` | [§3.0](creation.md) (#755) |
| A pickup-point order's delivery address is its point's, from the carrier's catalogue (`address_of_pickup_point`), never the buyer's: the receipt, the ACS voucher and the order page read that one snapshot. Only a deliverable point is accepted: in the catalogue, active and in the order's country; a missing or withdrawn one is looked for once by a guarded, deadline-bounded resync, run after every cheaper check, then refused. The order keeps none of the buyer's region, floor, location type or address line 2, and its owner cannot write one over the point's (`Order.SHIPPING_ADDRESS_FIELDS`) | [§3.3](creation-address.md) (#642) |
| An order has at most one payment link a buyer can pay: `create_checkout_session` returns the order's live link again, refuses with 409 `payment_in_progress` while a paid link waits for its webhook and 503 `payment_provider_unavailable` when the provider cannot say, and mints only when none can be paid; a hosted session and a PaymentIntent are never open together | [§4](payments.md) (#643) |
| A pay way is accepted exactly when the quote lists it for the delivery: `OrderQuoteService.offered_pay_ways` is the one rule behind the quote's `payWays`, the selection of a quote, and `OrderViewSet._validate_pay_way_for_order` at order creation | [§3.0](creation.md) (#635) |
| "Free above X" means one value for shipping and for the pay-way fee: the products after discounts, excluding shipping and fees (`OrderQuoteService.threshold_value`), passed by every caller of `shipping_cost` and `calculate_payment_method_fee` | [§5.0](shipping.md) |
| A payment is bound to what it was made for: every PaymentIntent carries server-set `purpose` (`order` / `giftcard`) / `subject` / `tenant_schema` metadata, the webhook finds an order's intent by it only for an existing order of this store, a gift-card intent pays no order, and the polled status path settles only through `handle_payment_succeeded` | [§4.1](payments.md) |
| A succeeded Stripe PaymentIntent of this store (our binding metadata, this tenant) that no order carries (not by `payment_id`, not by its binding, and not a gift-card purchase's) pages staff with `ops.payment_without_order`, once per intent: queued after the webhook commits, claimed on the intent id before the send, retried on a transient mail error, at once (every intent of ours is opened for an existing order or a gift-card purchase); an intent without our binding is only logged | [§4.1](payments.md) (#818) |
| A confirmed charge settles an order only for its own total: Stripe's collected amount and Viva's amount AND currency are compared (a missing one refuses), a mismatch is booked, flagged (`AMOUNT_MISMATCH_FLAG`), alerted and never auto-canceled | [§4.1](payments.md), [§4.3](payments-viva.md) |
| A reversal of a gift-card purchase's payment (refund, claim, chargeback, Stripe or Viva) voids the unspent card and records the spent part | [§4.2.1](payments.md) |
| `Order.payment_status` / `payment_id` are read-only in the admin, and the owner PATCH/PUT cannot change a priced order's country or region | [§7](cancel.md) |
| ACS voucher mint uses 3-phase claim → API → persist with 300s TTL, and a voucher request of unknown outcome (lost answer, unclassified error, expired claim) is never sent again: `voucher_request_unknown_at` holds every caller until staff adopt the voucher or request again | [§5.1](shipping-acs.md), `docs/acs-debugging.md` |
| While an `AcsVoucherHold` is in force no `ACS_Create_Voucher` is sent for the store, and a held request is never a request of unknown outcome: it is stamped `voucher_request_held_at`, keeps no claim, and only `issue_held_acs_vouchers` sends it, one request first | [§5.1](shipping-acs.md), `src/shipping_acs/voucher_hold.py` |
| Webhook payment_status writes never regress a SETTLED state (`SETTLED_PAYMENT_STATUSES` = COMPLETED/REFUNDED/PARTIALLY_REFUNDED/CANCELED). Stripe/Viva events are unordered + may duplicate, so `handle_payment_failed`/`_handle_payment_failed` skip when already settled, and `handle_payment_succeeded`/`viva_settlement.handle_payment_created` skip when already REFUNDED/PARTIALLY_REFUNDED/CANCELED. Reversal (COMPLETED→REFUNDED) is left intact. | `src/order/enum/status.py` `SETTLED_PAYMENT_STATUSES` |
| A poll of the provider writes only an open payment's state: a canceled attempt is recorded FAILED (never CANCELED on an open order), a refunded state never. A provider that could not be asked raises `PaymentStatusUnavailableError` and nothing is written, never FAILED | [§4.1](payments.md) (#697, #698) |
| A Stripe refund carries `idempotency_key=order-<id>-refund-<n>`, `n` counted from the ledger under the order lock. A refund the provider did not take on (`requires_action`, `failed`, `canceled`) records nothing and never moves the order to PROCESSING | [§7.2](refund-execution.md) |
| `refund_order` returns a `RefundResult` (what actually moved), never a yes/no: a split refund declined part-way keeps the shares already made and reports them, and every caller (the staff action, `cancel_order`) says so instead of "nothing was refunded" | [§7.2](refund-execution.md) |
| The refund confirmation email is one per REFUND ledger row, claimed by inserting a `RefundNotification` BEFORE it is sent (unique per refund), and the claim is released when the send fails for good. It is never decided by comparing counts: that let two workers both send and let one task swallow another refund's email | [§7.2](refunds.md), [§7.3](payment-ledger.md) |
| A COD order's payment status and its `COD_COLLECTION` ledger row are written in ONE atomic step under the order lock (`OrderService.settle_cod_payment`), the status read under that lock. A failed insert rolls the status back, so a payout retry never skips an order that is paid but unrecorded | [§4.4](payments.md) |
| The "shipped" email/toast fires only at genuine SHIPPED (status==SHIPPED **and** tracking present), never at voucher-mint; PROCESSING is internal-only (no customer email/toast). `send_shipping_notification_email` self-gates. | [§6.1](emails-and-notifications.md) |
| Admin-WYSIWYG fields in emails render `\|safe` (.html) / `unescape(strip_tags())`+`\|safe` (.txt — Django autoescapes .txt too) | `.claude/rules/domain-patterns.md` "Transactional email rendering" |

These invariants need more than a table row. Each ends with its anchor.

- The backend uses the `psycopg` pool with `CONN_MAX_AGE=0` under ASGI
  (`DB_POOL_*` in `src/settings/database.py`). With persistent connections, asgiref's
  per-request thread executor leaves each thread's connection open until the
  thread is collected, which exhausts Postgres `max_connections` under load.
  The pool bounds the connections of a process, and Django refuses a pool
  beside a non-zero `CONN_MAX_AGE`
  - Anchor: commit `8041546ce` (2026-04-06)
- `ShippingService.dispatch_create_shipment_task` wraps the carrier dispatch
  in `transaction.on_commit`. Queued inside the order-creation transaction,
  the task looked the order up on another connection before the commit, did
  not find it and gave up, so the order kept no voucher (prod order 47,
  2026-04-30). Outside a transaction `on_commit` runs at once, so the admin
  buttons need no special case
  - Anchor: commit `59527a873` (2026-04-30),
    `tests/unit/shipping/test_dispatch_helpers.py`
- Fresh order columns are read with
  `Order.objects.filter(pk=...).values(...).first()` — NOT
  `refresh_from_db(fields=...)` and not `.only(...)`. Either leaves the other
  columns deferred, and `Order.__init__` loads them when it snapshots
  `_original_status`, `_original_tracking_number` and the rest, which recurses
  through the manager without end
  - Anchor: commit `6521f8f2a` (2026-05-01), the docstring of
    `OrderLifecycleService.maybe_advance_to_completed`
- `suppress_customer_status_notifications` on chained transitions. A carrier
  DELIVERED that completes the order, a payment that moves it to PROCESSING
  (the confirmation email follows at once) and tracking added from PENDING
  each produce two customer messages milliseconds apart: without the
  suppression the customer gets two emails and two toasts that say the same
  thing
  - Anchor: [§6.3](emails-and-notifications.md), commit `c82711884`
    (2026-05-01)
- ACS COD numeric fields use Greek-locale (comma decimal). ACS reads a dot as
  the thousands separator: `47.01` is taken as 4701 and refused by the 1500 €
  cash cap, and a weight of `0.5` prints as 5 kg and is billed as such
  (`_kg_from_grams` and the `Cod_Ammount` line of
  `_build_create_voucher_params` in `src/shipping_acs/services.py`)
  - Anchor: commits `61dd09854` (2026-04-29, the amount) and `ffe1718e4`
    (2026-04-30, the weight)
- The default cache fails open on a Redis outage (reads miss, writes no-op —
  `src/core/cache_backend.py`), but never for keys under `STRICT_KEY_PREFIXES`: the
  `strict:` throttle namespace, allauth's rate limits, TOTP replay protection,
  the IdP's single-use codes and WebSocket tickets. The checkout, payment,
  coupon and gift-card throttles (`src/order/throttles.py`,
  `src/cart/throttles.py`, `src/giftcard/throttles.py`,
  `fail_closed = True`) therefore REFUSE with 503 `temporarily_unavailable`
  during an outage (`src/core/middleware/strict_cache.py`; never a 429: the
  caller sent nothing too often), while
  browsing/search/cart throttles and DRF's default `anon`/`user` budgets allow
  - Anchor: `src/core/api/throttling.py` module docstring
- The tenant resolve payload and domain set are invalidated by
  `Tenant.cache_generation`, never by a Redis delete: it is part of their keys,
  and `Tenant.save`, every `TenantDomain` write and the folded pay-way /
  agent-setting writes bump it in the database inside the write's transaction.
  A delete during an outage is a fail-open no-op, so purge-by-delete left the
  pre-write payload served for up to its TTL; strict keys would have made the
  READS raise, and resolve is on every storefront request
  - Anchor: `src/tenant/cache.py` module docstring
- Gift-card value a staff member creates in one step (a new card, or a positive
  balance adjustment) is capped per store by `GIFT_CARD_STAFF_ISSUE_LIMIT`
  (default 500, `0` = off). Above it only the store's OWNER or a platform
  superuser may do it (`GiftCardService.over_staff_limit`; an ADMIN may not);
  the ledger row says "above the staff limit ... authorised by the owner",
  `created_by` names the person, and an adjustment adds an admin log entry. A
  refusal is logged. Enforced in `giftcard.admin` (the add form and
  `adjust_balance`). Steps repeat, so one staff member is also capped over any
  rolling 24 hours by `GIFT_CARD_STAFF_DAILY_LIMIT` (default 1000, `0` = off):
  the positive ISSUE and ADJUST rows with their `created_by` in the last day
  plus the new amount (removals do not offset it; purchased cards have no
  `created_by`). Same authority rule and the same audit ("above the staff daily
  limit ... authorised by the owner", admin log on an adjustment). The total is
  read and the row written in one transaction under a per-member advisory lock
  (`GiftCardService.lock_staff_issuing`), so two parallel requests cannot both
  pass
  - Anchor: `src/giftcard/services.py`, `src/giftcard/admin.py`
- `order_refunded` always carries `amount`, and the gift-card redemption and
  the spent points go back only as the value of a refund beyond the provider's
  money (`refund_order`, value-based, [§7.2](refunds.md)) or on a cancel, never
  because the provider's share was refunded; the points an order earned are
  reversed once, when `payment_status` is REFUNDED. A Viva 1797 is applied from
  the REFUND transaction (`F`, refund/void type, negative amount), not from a
  REFUNDED status that Viva never reports
  - Anchor: [§4.3](payments-viva.md), [§7.2](refunds.md)
- Money moved for an order is recorded on the append-only `PaymentTransaction`
  ledger, once, keyed on the provider's own id
  (`UNIQUE (provider, provider_reference, kind)`). What can still be refunded
  is read from it under the order row lock, and `PaymentLedger.refund_status`
  is the ONE place that decides REFUNDED versus PARTIALLY_REFUNDED: no path
  writes either status for itself, and none re-derives it from
  `Order.amount_due` (the amount owed at creation) or from a provider's refund
  `status`. A refund is as many `refund_order` calls as it takes, never past
  the remainder
  - Anchor: [§7.2](refunds.md), [§7.3](payment-ledger.md)
- The platform issues retail myDATA documents only: an 11.1 receipt when a
  RECEIPT order ships (`order_shipped`), or, for a courier or locker order of
  a store with its dispatch point set, with movement indication before the
  pickup, once the carrier holds its voucher or parcel and the order awaits
  no online payment (cash on delivery included: it is not awaiting one)
  (`order_shipment_dispatched`, `receipt_issuable`,
  [§7.4](fiscal-mydata.md)); exactly once either way. An 11.4 credit per
  ledger REFUND row of an order whose receipt has a MARK, plus one settlement
  credit when the order closes without a sale. `document_type=INVOICE` orders
  submit nothing: invoices are issued by hand in timologio (AADE decision
  Α.1128/2025 art. 1(2)). A document AADE did not register is never emailed. A
  credit's net is cumulative so partial credits never pass the receipt's net
  (our own cap; AADE does not check)
  - Anchor: [§7.4](fiscal-mydata.md)
- `Order.amount_due` is what the customer OWES, fixed at creation
  (`calculate_order_total_amount()`); it is never read as money collected. What
  is still to be paid is `Order.remaining_amount`
  (`pricingBreakdown.remainingAmount`): `amount_due` until `payment_status` is
  settled, 0 after, and never derived from the ledger (a settled order can hold
  no ledger row). Whether an order is paid is `payment_status` alone
  (`Order.is_paid`, the `is_paid` filter). It was called `paid_amount` until
  `0070_order_amount_due` (the columns were dropped by
  `0071_order_drop_paid_amount_columns`, and `paidAmount` is gone from the
  API), and every reader that took it for collected money was wrong
  (`remainingAmount` was 0 on every order, the filter matched every unpaid
  online order)
  - Anchor: [§7.3](payment-ledger.md)
