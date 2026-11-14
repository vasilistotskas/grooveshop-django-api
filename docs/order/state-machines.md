# Order system: state machines

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 2. State machines

### 2.1 OrderStatus transitions (the `allowed_transitions` map in `OrderService.update_order_status`)

```
PENDING → PROCESSING → SHIPPED → DELIVERED → COMPLETED
   │           │           │          │
   │           │           │          └──► RETURNED → REFUNDED
   │           │           └──────────► RETURNED → REFUNDED
   │           └──► CANCELED   (cancel_order only)
   └──► CANCELED               (cancel_order only)
```

- `OrderService.update_order_status(order, new_status)` validates
  every transition against this table.
- **CANCELED is not in the table.** A cancel releases stock, settles or refunds
  the payment and records `metadata['cancellation']`, so the only way into
  CANCELED is `OrderService.cancel_order` ([§7.1](cancel.md)).
  `update_order_status(order, CANCELED)` raises `InvalidStatusTransitionError`
  for every caller, and the staff endpoint
  `POST /api/v1/order/{id}/update_status` answers it with 400
  `cancel_through_cancel_action` ("An order is canceled through its cancel
  action…"). Until this was closed the endpoint wrote the label alone: the
  stock stayed held and a paid order kept the customer's money
  (`tests/integration/order/test_update_status_does_not_cancel.py`).
- Terminal states: `COMPLETED`, `CANCELED`, `REFUNDED`. No exit.
- `→ REFUNDED` is refused (`RefundOutstandingError`, in `update_order_status`,
  so the admin "Mark as refunded" action and every other caller) while
  `PaymentLedger.refund_open(order)` (refundable money, or a paid provider
  order whose capture was never recorded): the status says the customer was
  paid back, and the loyalty reversal and the "your refund has been processed"
  status email follow it. Refund first ([§7.2](refunds.md)); an order with
  nothing collected (a refused parcel that came back) closes freely. An order
  paid entirely with gift cards and points has no provider payment, and it is
  open until its stored value has gone back (`refund_open` counts it): staff
  refund it through the Refund action (value-based, [§7.2](refunds.md)), which
  credits the cards and restores the points, and only then can it be closed as
  REFUNDED.
- Auto-transitions wired in code:
  - `PENDING → PROCESSING` on COD voucher mint
    (`AcsService._advance_pending_order_to_processing`,
    `BoxNowService._advance_pending_order_to_processing`).
  - `PROCESSING → SHIPPED` from carrier events
    (`_apply_order_status_transition` in both services).
  - `SHIPPED → DELIVERED` from carrier events.
  - `DELIVERED → COMPLETED` for paid orders
    (`OrderService.maybe_advance_to_completed`), applied inline by the carrier
    poll and the COD reconcile and swept hourly by
    `order.tasks.complete_paid_delivered_orders` for everything that lands
    outside those two moments (a payment entered by hand, a webhook after
    delivery, an inline attempt that failed once — prod orders 252, 253, 73, 92
    on 2026-09-20).
  - `PROCESSING → SHIPPED` also from an ACS `attempted` state: a failed
    delivery attempt proves the parcel shipped (prod order 267 sat at
    PROCESSING for the ten days ACS held it). And a shipment the summary still
    calls `new` is lifted to `in_transit` when `ACS_TrackingDetails` holds any
    checkpoint that is not a print (`_has_left_sender`), because ACS reports
    `shipment_status=5` / `delivery_flag=0` / no reason for a parcel waiting at
    a Smartpoint (prod order 284).
  - A CANCELED shipment state (only ever produced by the admin retire action or
    a voucher cancel, never by tracking) routes the order through
    `OrderService.cancel_order` (`OrderService.cancel_for_dead_shipment`,
    `refund_payment=False`: a paid order logs a WARNING, shows in "Canceled
    orders to refund" (`OrderQuerySet.refund_owed`) and staff refund it) —
    stock back, payment settled, cancellation recorded — never a bare status
    write. Reached from `manage.py reconcile_acs_order_status`, and only for an
    order not yet shipped (`AcsService.order_status_for_shipment_state`): a
    voucher retired after the order shipped is a fact about the voucher, so the
    order keeps its status and the replay lists the pair as its own
    informational line (dry run and `--apply`;
    `AcsService.voucher_retired_on_shipped_order`), not as drift.
  - A BoxNow `canceled` parcel event (webhook or poll) **never moves the
    order** (`BoxNowService._apply_order_status_transition`). BoxNow defines it
    as "Parcel has been canceled by the sender" (Webhook-Based Parcel Tracking
    Guide v1.4.6, p. 2; API Manual v7.2, §6.5.3, p. 49), and the sender is the
    store: the event reports a decision the store already made. The shipment is
    recorded CANCELED. An order that was canceled has nothing left to do (its
    `cancel_order` already ran); a PENDING / PROCESSING order keeps its status,
    its stock and its payment, and needs follow-up (§2.3) until staff create a
    new parcel ("Create BoxNow parcel now", [§5.2](shipping-boxnow.md)) or
    cancel the order; a shipped one is logged and left alone. Owner's decision
    of 2026-11-10 (#600), which reversed v3.242.6, where the event canceled the
    order through `cancel_for_dead_shipment`: a staff member who canceled a
    parcel to replace it lost the order, and the event could cancel an order
    before the customer's own cancel (which refunds) reached its lock.
  - Carrier bridge: when a carrier reports DELIVERED or RETURNED while the
    order is still PENDING/PROCESSING (state jump between polls / missed
    webhook), `_apply_order_status_transition` walks the missing SHIPPED step
    first. The SHIPPED hop is customer-silent on the RETURNED path
    (`update_order_status(..., silent_for_customer=True)`) — prod orders 179 &
    189 (2026-07) were stuck because the direct PROCESSING → RETURNED was
    rejected forever.

### 2.2 PaymentStatus transitions

No explicit table — flips are direct assignments. Common paths:

| From → To | Trigger |
|---|---|
| `PENDING → COMPLETED` | `Order.mark_as_paid()` (called by Stripe + Viva success handlers, COD reconcile) |
| `PENDING → FAILED` | `OrderService.handle_payment_failed` (Stripe `payment_intent.payment_failed`) |
| `FAILED / PENDING / CANCELED → PENDING` | `POST /order/{id}/retry-payment` (`OrderViewSet.retry_payment`) mints a fresh Stripe PaymentIntent and reopens the payment — **only while `Order.status` is `PENDING`**. (continues under the table) |
| `COMPLETED / PARTIALLY_REFUNDED → PARTIALLY_REFUNDED / REFUNDED` | Every refund path writes a `PaymentTransaction` REFUND row and `PaymentLedger.record_refund` derives the status from the ledger's sums in that one place ([§7.3](payment-ledger.md)): `OrderService.refund_order` (the in-app refund, the staff "Refund" action, a cancel), `handle_stripe_refund_made` ([§4.2](payments.md)) and the Viva reversal webhook (`_handle_reversal_created`, event 1797, [§4.3](payments.md)). (continues under the table) |
| `PENDING / PROCESSING / FAILED → CANCELED` | `Order.save()` on the `status → CANCELED` **or `RETURNED`** transition (`PAYMENT_CLOSING_STATUSES`, `Order.settle_unpaid_payment`, `src/order/models/order.py`) — one save shared by the service, the admin form and any script, never a second save from the cascade (that re-fires the transition). (continues under the table) |
| `PENDING → COMPLETED` | `AcsService._mark_cod_order_paid_if_pending` (COD reconcile) |
| `CANCELED → COMPLETED` on a CANCELED order | `OrderService.record_payment_after_cancel` — a confirmed Stripe (`handle_payment_succeeded`, `checkout.session.completed`) or Viva (`_handle_payment_created`) charge for an order that is already canceled. (continues under the table) |

**`FAILED / PENDING / CANCELED → PENDING`.** A CANCELED payment on a PENDING
order (an intent canceled while the order waits) is retryable. Every endpoint
that starts a payment (`create_payment_intent`, `create_checkout_session`,
`retry-payment`, `confirm_agent_payment`) shares
`OrderViewSet._payment_start_refusal`: a paid order gets 400 "This order has
already been paid.", a CANCELED or otherwise advanced one 400 "This order is no
longer open for payment." — before the provider is called, because
`cancel_order` settles an unpaid payment to CANCELED too, and reopening it let
a charge land on a dead order. The provider call itself runs unlocked;
`PayWayService._record_opened_payment` then locks the row and, if its status,
payment status or payment id changed meanwhile (a cancel, a webhook for the
previous intent), raises `OrderChangedDuringPaymentError` — the view answers
409 and the new intent's client secret is never returned, so it can never be
confirmed.

**`COMPLETED / PARTIALLY_REFUNDED → PARTIALLY_REFUNDED / REFUNDED`.**
PARTIALLY_REFUNDED while part of what was captured is still refundable,
REFUNDED once none is. An order can go through PARTIALLY_REFUNDED any number of
times.

**`PENDING / PROCESSING / FAILED → CANCELED`.** An order that closes unpaid
owes nothing, so its financial state is final too: a canceled order
(`0057_settle_canceled_unpaid_orders` backfilled 78 rows) and a refused or
uncollected COD parcel that came back RETURNED
(`0058_settle_returned_unpaid_orders` backfilled 34). A PAID return keeps
COMPLETED until the refund moves it to REFUNDED.

**`CANCELED → COMPLETED` on a CANCELED order.** Checked **before** the
settled-state guard (`OrderService.is_payment_after_cancel`), because a
canceled order always carries a settled payment and behind the guard the charge
was dropped with a WARNING. Books the money (`mark_as_paid`), records
`metadata["payment_after_cancel"]`, logs ERROR, emails ops, notes the order;
the order stays CANCELED and nothing ships. Idempotent per payment id. A
REFUNDED / PARTIALLY_REFUNDED canceled order still ignores a stale success.

`Order.clean()` refuses a COMPLETED payment with `amount_due` 0.00
unless deductions cover the whole total (`Order._payment_consistency_errors`)
— the admin form edits both fields. It is not a `CheckConstraint`: the
total is a sum over the order's lines.

### 2.3 The follow-up flag (`src/order/lifecycle/follow_up.py`)

A shopper who ordered N calendar days ago and still has no parcel is about
to write in, and an open order whose parcel was canceled ships nothing
until someone decides. The admin flags both. It is computed, never stored:
no column, job or email, and the flag disappears by itself when the order
is delivered, completed, returned, canceled or refunded, or when its
canceled parcel is replaced.

An order needs follow-up when it is not an online order still waiting for
its payment (below) and **either** of these holds:

- **It waited.** It was placed on a store-timezone day at least
  `ORDER_FOLLOW_UP_DAYS` days ago (store setting, default 7, `0` switches
  this part off). Whole calendar days: an order placed on day D is flagged
  from 00:00 on day D+N, not 24 h × N after it was placed
  (`follow_up_cutoff`). Its status is PENDING, PROCESSING or SHIPPED
  (`OrderStatus.get_status_groups()` `needs_processing` + `in_transit`).
- **Its parcel was canceled while it is open.** Its status is PENDING or
  PROCESSING and its carrier shipment is canceled, whatever its age and
  whatever `ORDER_FOLLOW_UP_DAYS` says (`canceled_shipment_q`). Each
  carrier answers through its adapter
  (`ShippingCarrierInterface.canceled_shipment_q`, OR-ed by
  `ShippingService.canceled_shipment_q`, so `order` never imports a
  carrier app): BoxNow matches a `canceled` parcel, which leaves its
  order open (§2.1); ACS keeps the default `None`, because its canceled
  shipment cancels the order (`cancel_for_dead_shipment`). Staff create a
  new parcel ("Create BoxNow parcel now", which moves the shipment back to
  `pending_creation`) or cancel the order; either clears the flag.

The unpaid-online exclusion, for both:

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
carrier's last event (`AcsShipment` / `BoxNowShipment.last_event_at`); for
a canceled parcel the line says so and names the two ways out instead of a
date (`has_canceled_shipment`). The counter is a row of the dashboard's
"Needs attention" list linking to the filtered list; a `BoxNowShipment`
write refreshes it (`depends_on`). The Orders sidebar badge keeps the
pending count.
