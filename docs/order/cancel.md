# Order system: cancellation

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7. Cancellation + refund flows

This file holds the cancel paths. Refunds are in [`refunds.md`](refunds.md)
and [`refund-execution.md`](refund-execution.md) (§7.2), the payment ledger
in [`payment-ledger.md`](payment-ledger.md) (§7.3) and the tax documents in
[`fiscal-mydata.md`](fiscal-mydata.md) (§7.4).

### 7.1 Cancel paths

- **Customer** (the owner, or a guest holding the order's uuid; a guest's uuid
  works for `GUEST_ORDER_ACCESS_DAYS` after the order, see
  `Order.opens_for_guest`): `POST /api/v1/order/{id}/cancel` →
  `OrderService.cancel_order(order, reason, refund_payment=True, customer_initiated=True)`.
  The courier is asked first, see §7.1.1.
- **Agent gateway:** `GET /api/v1/agent/internal/order/{uuid}`, with its shared
  secret in `X-Internal-Token`, reads an order an agent placed
  (`OrderAttribution` source type `agent`; any other order answers 404),
  whatever its age or owner, because it reports an order to the platform for as
  long as the order moves. A uuid a client hands the gateway goes to the guest
  route instead and keeps its limit.
- **Store staff**: the same endpoint, with `customer_initiated=False` — the
  view takes it from `is_store_staff(request.user)`, never from `is_staff`.
- **Admin**: same `cancel_order` (`customer_initiated=False`) via the "Cancel
  selected orders and restore stock" action. The change form's `status` field
  is **read-only**: a form save wrote the column straight to the row, bypassing
  both `cancel_order` (no stock restore — prod orders 242 and 213, 2026-09-08)
  and the transition table in `update_order_status`. Every other transition is
  an action too (`mark_as_processing` … `mark_as_returned`,
  `mark_as_refunded`), routed through `update_order_status`. RETURNED and
  REFUNDED deliberately leave stock alone ([§5.1](shipping-acs.md)).
- **Auto**: `auto_cancel_stuck_pending_orders` Celery beat (every 15 minutes) —
  cancels an unpaid online order once its payment hold has ended
  (`customer_initiated=False`), see §7.1.0.
  An unpaid online order holds its stock until then, so how many one customer
  can have waiting is bounded per store: `ORDER_MAX_UNPAID_ONLINE_ORDERS`
  (default 5, `0` = off; `OrderService._enforce_unpaid_order_limit`, checked in
  `create_order_from_cart_offline` for online methods only). The customer is
  the account, or the email (case-insensitive) for a guest; PENDING+PENDING
  online orders count, offline (COD) and paid ones do not. An order over the
  limit is refused with a plain message. Counted under the cart lock, so two
  carts of one customer racing can overshoot by the number in flight.
  An order the agent gateway places has a second bound, per cart: none while
  an earlier order of that cart awaits its online payment
  (`cart_has_unpaid_order`, [§3.2](creation.md)).
- **Carrier-driven (ACS only)**: `OrderService.cancel_for_dead_shipment`
  (`customer_initiated=False`, `refund_payment=False`; the shipment is already
  canceled), called by the ACS status sync for a CANCELED shipment of an order
  not yet shipped ([§2.1](state-machines.md)). The courier is not asked to
  cancel, before or after: the order records
  `shipment_cancel = {attempted: false, dispatched: true}`
  (`SHIPMENT_ALREADY_CANCELED`), so no courier call runs inside the tracking
  sync's transaction. An order whose status no longer allows a cancel raises
  `OrderNotCancelableError`; any other `OrderCancellationError` is a cancel
  that failed on the way.
- **Not a cancel path: a BoxNow `canceled` parcel event.** It records the
  shipment CANCELED and leaves the order to the customer, staff or the sweep
  above ([§2.1](state-machines.md)). An open order behind it needs follow-up
  ([§2.3](state-machines.md)). So a customer cancel whose parcel event lands
  before the order row is locked (§7.1.1, step 4) still cancels and refunds:
  before #600 the event canceled the order first, without a refund, and the
  customer's own cancel then failed its status check.

There is no other path. `update_order_status` refuses CANCELED
([§2.1](state-machines.md)), the admin change form cannot write `status`, and
the order write serializer keeps it read-only, so `cancel_order` is the only
code that sets it on an existing order. The `order_canceled` receiver that used
to backfill `metadata['cancellation']` and cancel the voucher for a status
written some other way (`handle_order_canceled`) is gone with the last such
path; `order_canceled` still fires on every cancel for the gift-card credit and
the loyalty reversal.

`customer_initiated` is a required decision for every caller that is not a
customer: it defaults to `True`, so a caller that forgets to choose is held to
the courier's answer (§7.1.1) rather than silently skipping it.

`cancel_order`:
0. Customer only: asks the courier to cancel the voucher before anything else
   (§7.1.1). No lock is held and no transaction is open for that call.
1. Locks order row.
2. Locks every product it touches in pk order (`StockManager.lock_products`,
   [§3.1](creation.md)), then releases stock + reservations
   (`StockManager.increment_stock`, `release_reservation`).
3. Sets `status=CANCELED` (the save settles an unpaid `payment_status` to
   `CANCELED` — `Order.settle_unpaid_payment`), records
   `metadata['cancellation']`.
4. Staff / system only: cascades to the courier voucher via
   `cancel_attached_shipment` → `ShippingService.cancel_shipment` (PR #2 H).
   Records the dispatch outcome on metadata. A carrier rejection (e.g., voucher
   already in pickup list) does not stop the cancel: it is logged, kept in
   `metadata['cancellation']['shipment_cancel']['error']`, left as a staff-only
   `OrderHistory` NOTE
   (`Order canceled, but the courier did not cancel the parcel: …`), and the
   admin "Cancel selected orders" action raises an error message for it. For a
   customer cancel the courier already confirmed in step 0, so the outcome is
   recorded up front and the cascade skips.
5. After the cancel has COMMITTED, optional refund via `refund_order`
   (`OrderService._refund_canceled_order`; `refund_payment=True`, and the order
   is paid or the ledger still holds refundable money, so an order a provider's
   dashboard already refunded in part gets the rest back, not nothing). The
   refund is its own claim, call and record ([§7.2](refund-execution.md)): no
   transaction is open while the provider answers, which a cancel used to hold
   open across the call. A refund refused before any provider is asked (a
   dispute is open, nothing left, no credentials) is written to the order's
   history in plain words and reported as
   `refund_info = {"refunded": False, ...}`; for a dispute
   `refund_info["dispute_open"]` is true, the history says the refund waits
   until the dispute closes, and the admin cancel action says the same.

**Don't bypass**: do not call `order.save(update_fields=['status'])` directly
to cancel — you'll skip the stock release, the refund, the cancellation record
and the courier, and nothing catches it afterwards. Always go through
`OrderService.cancel_order`.

### 7.1.0 The payment hold: stock is released when the payment link expires

An online-payment order reserves its stock at creation, so how long it may wait
for its payment is how long anyone can lock stock without paying. The hold is
**short, and ends exactly when the payment can no longer succeed**
(`src/order/payment_hold.py`). Releasing earlier would let a payment succeed on
an order whose stock is gone (a paid-but-canceled order,
[§2.2](state-machines.md) `record_payment_after_cancel`); releasing later only
locks stock.

- **One window, handed to the provider.** `ORDER_ONLINE_PAYMENT_HOLD_MINUTES`
  (per store, default 30; the validator accepts 30 to 1092) is passed as the
  expiry of the payment link or session on every path that opens a payment:
  `OrderViewSet.create_checkout_session` (hosted Stripe Checkout Session and
  Viva payment order), and `PayWayService.process_payment`, which the
  `create_payment_intent` and `retry-payment` endpoints use.
  `confirm_agent_payment` (UCP/ACP delegated payment) charges at once and opens
  nothing that waits. The payment-first Stripe path ([§3.1](creation.md))
  creates no order until the money has moved, so it holds nothing.
- **What each provider does with it.**
  - Viva Smart Checkout: `paymentTimeout` on `POST /checkout/v2/orders`,
    **seconds**, default 1800, `65535` = never expires. After it expires the
    shopper must return to the store and start checkout again, and Viva sends
    **no** webhook for the expiry. Before this change the store never sent it,
    so every Viva link already died after 30 minutes while the order kept its
    stock for 24 hours.
  - Stripe Checkout Session: `expires_at`, a Unix timestamp **30 minutes to 24
    hours** after Stripe receives the request (default 24 hours). A request
    closer than 30 minutes is refused, so the provider asks for at least 31
    minutes; the default 30 minute hold therefore lasts 31 minutes on Stripe.
    An expired session sends `checkout.session.expired`, and a customer opening
    it sees an expired message.
  - Stripe PaymentIntent (the order-first `create_payment_intent` /
    `retry-payment` flow): a PaymentIntent has no expiry, so the sweep cancels
    it (`StripePaymentProvider.close_payment`, reason `abandoned`) before it
    releases the stock. An intent Stripe will not cancel (processing,
    succeeded) leaves the order open for its webhook; the sweep reports
    `skipped_open`. A retry closes the intent it replaces the same way before
    it opens the new one (`PayWayService.process_payment`): otherwise the old
    intent's client secret could still be confirmed after the sweep released
    the stock. An intent past the point of no return refuses the retry
    (`previous_payment_in_progress`), and a provider outage refuses it too
    (`previous_payment_not_closed`).
- **The expiry actually applied is the one stored.** The provider returns
  `expires_at` (Stripe's own value; for Viva, the reply time plus the timeout,
  never earlier than Viva's clock) and the caller stores it in
  `Order.payment_hold_expires_at`, so the two always end together even where
  the provider moved it. A new link or session replaces it. Until a payment is
  opened the hold runs from `created_at`.
- **The total is bounded.** `ORDER_ONLINE_PAYMENT_MAX_HOLD_MINUTES` (default
  60, at least the hold) caps the hold from the order's creation: a new link
  asks for `min(now + hold, created_at + max)` (Viva is told the remaining
  seconds), so retries cannot chain the stock lock. When no link fits any more
  the endpoint answers 400 `payment_window_expired` before the provider is
  called: for every start (`create_payment_intent`, `create_checkout_session`,
  `retry-payment`, `confirm_agent_payment`) once the limit has passed, and for
  a hosted session also when less than the provider's `min_hosted_session` is
  left (Stripe: 31 minutes, because a shorter session cannot be opened and a
  longer one would outlive the limit). A retry inside the window works as
  before. The shopper places a new order from their cart: the cart is kept
  while an order awaits its payment and a cancel leaves it alone. Orders
  stamped by migration `0083` carry `created_at + 24h`, so a retry on one older
  than the maximum is refused too.
- **The sweep.** `ORDER_ONLINE_PAYMENT_GRACE_MINUTES` (default 5) is added to
  the stored instant, and the 15 minute beat adds its own slack, so a
  confirmation already on its way reaches an open order. Only unpaid (`PENDING`
  / `PENDING`) orders of an `ONLINE` pay way are swept; COD and
  carrier-collected orders are not ([§4.4](payments.md)). A `PENDING` order
  whose payment FAILED is swept too, `ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`
  (default 30) after the failure: a failed attempt does not end the payment,
  the shopper may confirm the same PaymentIntent with another card or retry the
  same Viva payment order. Both buckets ask the provider to close the payment
  first (`close_payment(order)`): Stripe cancels the PaymentIntent and the
  order is canceled at once, whatever its hold says. A provider with nothing to
  cancel (Viva, whose payment order stays payable after a failed transaction
  until its own expiry; a hosted Stripe Checkout Session, which records no
  `payment_id` and ends at its `expires_at`) is closed once the stored hold
  (`payment_hold_expires_at`, plus the grace window) has ended and not before.
  A provider that cannot be built (its credentials were removed) cannot close
  anything and takes the same wait. A payment that is not closed (that wait, or
  a Stripe intent that succeeded or is processing) leaves the order open for
  its webhook and counts `skipped_open`.
- **A payment that still arrives after the cancel** is not lost: Viva
  `_handle_payment_created` and Stripe `handle_payment_succeeded` /
  `checkout.session.completed` route it to
  `OrderService.record_payment_after_cancel` ([§2.2](state-machines.md)): the
  money is booked, the order stays CANCELED, ops is alerted and the order
  appears in "Canceled orders to refund". Stripe re-delivers a failed webhook
  for up to three days, and a late success is handled the same way.
- **Alert.** After each sweep, when `ORDER_UNPAID_ONLINE_ALERT_THRESHOLD`
  (default 25, `0` = off) or more online orders are still unpaid, the task logs
  `orders:unpaid_online_spike` at WARNING with `unpaid_online_orders` and
  `threshold`. The `GrooveShopUnpaidOnlineOrdersSpike` rule in
  `grooveshop-infrastructure` (`logs-alerting/vmrule-grooveshop-logs.yaml`)
  matches that message.
- **Replaces `ORDER_AUTO_CANCEL_PENDING_HOURS`** (the fixed 24 hours).
  Migration `0083` stamps the orders already waiting with `created_at + 24h` so
  none is released earlier than it was promised, and deletes the old setting
  row.

Sources:
[Viva payment order](https://developer.viva.com/apis-for-payments/payment-api),
[Viva Smart Checkout options](https://developer.viva.com/smart-checkout/smart-checkout-functionality-options),
[Stripe: managing limited inventory](https://docs.stripe.com/payments/checkout/managing-limited-inventory),
[Stripe: expire a Checkout Session](https://docs.stripe.com/api/checkout/sessions/expire),
[Stripe: cancel a PaymentIntent](https://docs.stripe.com/api/payment_intents/cancel).

### 7.1.1 A customer cancel asks the courier first

An order stays PENDING / PROCESSING from the moment its voucher exists until a
tracking poll sees the parcel move (voucher creation stores the tracking number
but does not advance the status). In that window the courier may already hold
the parcel, so a customer cancel is only accepted once the courier agrees:

1. `OrderService._ask_courier_to_cancel` re-reads the order's status (an order
   that no longer allows a cancel gets the usual status error and the courier
   is not asked), then calls `ShippingService.cancel_shipment` — the same
   carrier path a staff cancel uses (`AcsService.cancel_voucher`,
   `BoxNowService.cancel_shipment`). An order with no voucher has nothing to
   ask, and goes on; a BoxNow shipment with no parcel number whose creation
   was sent is first looked up at BoxNow by `orderNumber`, and a parcel
   found there is cancelled like any other ([§5.2](shipping-boxnow.md)).
2. If the courier confirms, the order cancels and refunds as usual, and
   `metadata['cancellation']['shipment_cancel']` is
   `{"attempted": true, "dispatched": <bool>}`.
3. If the courier does not confirm, `cancel_order` changes **nothing**: no
   status, no refund, no shipment change, no history row. Which answer the
   customer gets depends on why, and every case has the body
   `{"detail": <translated message>, "error": {"type": ...}}`
   (`OrderCancelErrorType`, `OrderCancelErrorSerializer`). Branch on
   `error.type`, never on `detail`.
   - **The courier refused** (an ACS voucher already on an issued pickup list,
     a BoxNow parcel past `NEW`, any other carrier business error):
     `ParcelWithCourierError`, 400 `parcel_with_courier`. The customer is told
     to contact the store about a return.
   - **The courier gave no answer** (unreachable, timed out, HTTP 5xx, an
     auth-layer rejection such as ACS's sporadic 406s, missing credentials, the
     same cancel already in flight): `CourierUnreachableError`, 503
     `courier_unreachable`. Nothing is known about the parcel, so the customer
     is told to try again in a few minutes.
   - **Anything else** is a fault of ours, not the courier's answer: it
     propagates and the endpoint answers 500 through its error log.

   The split rides the carrier exception hierarchy: every carrier app's errors
   derive from `shipping.exceptions.CarrierError`, and the no-answer ones
   (`AcsRetryableError` and with it `AcsAuthError`, `AcsConfigError`,
   `BoxNowRetryableError`, `BoxNowAuthError`, `BoxNowConfigError`) also from
   `CarrierUnavailableError`. A new carrier gets the same answers by deriving
   its errors the same way.
4. The courier is asked before the order row is locked, so the order can move
   on while it answers; the locked re-check then refuses the cancel with the
   usual status error. The shipment was already canceled by then (at the
   courier, or locally when no voucher had been issued), so the order is live
   with no voucher behind it; any other failure of the locked cancel ends the
   same way. `OrderService._note_shipment_canceled_without_order` logs an ERROR
   (the logged-errors alert) and leaves a staff-only NOTE
   (`The customer asked to cancel this order and its shipment was canceled, but the order itself was not canceled (its status is …)`)
   telling staff to create a new voucher. An order another request canceled in
   the meantime needs neither. A BoxNow order left that way also shows under
   follow-up ([§2.3](state-machines.md)) until it is re-issued or canceled.

Both carriers' cancel is idempotent on an already canceled shipment, so a
customer who retries after a cancel that stopped half way is not refused
(`BoxNowService.cancel_shipment` returns on a CANCELED parcel, as
`AcsService.cancel_voucher` already did).

**`canBeCanceled`** (order list and detail) is
`Order.can_be_canceled_by_customer`: the status rule (`Order.can_be_canceled`)
AND `ShippingService.can_cancel_shipment(order)`. The second half reads local
rows only, never the carrier: `AcsService.can_cancel_voucher` (false once the
voucher is on a pickup list, `AcsShipment.pickup_list`) and
`BoxNowService.can_cancel_shipment` (false once a parcel with a `parcel_id` is
past `NEW`). These are the exact guards the carriers apply before they call the
courier, so the flag and the cancel cannot drift;
`ShippingCarrierInterface.can_cancel_shipment` defaults to `True` for a carrier
with no cancel rule. A `True` is not a promise — the cancel request still asks
the courier. `Order.can_be_canceled` (status only) stays the rule for staff and
system cancels. The order list has no `canBeCanceled` filter: a status-only
filter beside a carrier-aware field would list orders whose own flag says no.
`OrderQuerySet.for_list()` selects `acs_shipment` / `boxnow_shipment` so the
list reads the flag without a query per order.
