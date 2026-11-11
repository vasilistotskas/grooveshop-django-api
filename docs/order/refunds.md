# Order system: refunds

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7.2 Refund paths

This file says what a refund returns. How `refund_order` plans a refund,
carries it out and settles an attempt of unknown outcome is the second half
of §7.2, in [`refund-execution.md`](refund-execution.md).

- **In-app**:
  `OrderService.refund_order(order, amount=None|Money, reason, *, lines=(), include_shipping=False, reason_code="")`.
  Reached from `cancel_order(refund_payment=True)` on a paid order (always
  `amount=None`, everything still refundable) and from the staff **"Return and
  refund"** dialog on an order (below). A refund is as many calls as it takes:
  `refund_order` can be called again until nothing is left.
- **Stripe dashboard**: webhooks `refund.created` / `refund.updated` →
  `handle_stripe_refund_made` ([§4.2](payments.md)).
- **Viva dashboard**: webhook 1797 → `_handle_reversal_created`
  ([§4.3](payments.md)). The same webhook also reports the refunds the in-app
  path makes; they are already on the ledger and add nothing.

Every path ends in `PaymentLedger.record_refund` ([§7.3](payment-ledger.md)),
so there is one rule for REFUNDED versus PARTIALLY_REFUNDED, and one
`order_refunded.send` per REFUND row recorded → `handle_order_refunded` → email +
WS notification + Meta Refund event (a share that goes back onto gift cards and
points sends `order_stored_value_returned` instead, §7.2 below). The email is
one per REFUND row: `handle_order_refunded` queues
`send_refund_confirmation_email(refund.pk)` on commit, and the task CLAIMS the
row before it sends by inserting a `RefundNotification` (unique per refund,
[§7.3](payment-ledger.md)). A task delivered twice, or a retry racing its
original, finds the claim and sends nothing, and one refund's task can never
stand in for another's. It replaces a count of ledger refunds compared with a
count of emails sent (`metadata["refunds_emailed"]`), which let two workers
both send and let one task mark another refund's email as done. The claim is
made before the send, so a worker that dies between the two leaves the refund
claimed and unsent (the price of never sending twice); a send that fails for
good releases the claim.

**Refunds are in the VALUE of what came back** (owner decision, 2026-10-18).
`refund_order(amount)` takes the value the customer returned, not a provider
amount. `PaymentLedger.refundable_value(order)` is what can still be refunded:
the provider's money (`refundable`) plus what the order paid with gift cards
and loyalty points that has not gone back (`stored_value_refundable`).

**The provider is paid back first; only the excess goes back onto gift cards
and points**, in proportion to what each has not yet got back, so both are used
up together. A EUR 100 order paid EUR 40 by card and EUR 60 by gift card, a EUR
10 item returned and EUR 10 refunded, puts EUR 10 on the card and NOTHING on
the gift card; before this, refunding the card's share released the whole EUR
60 (the HIGH finding of the fraud audit). Refunding everything is
`refund_order(order)` (no amount: what the ledger says is still refundable);
the "Return and refund" dialog has no such default, it asks for lines or a
goodwill amount and shows the refundable remainder, split into provider money
and stored value, in the goodwill field's help text. In code: step 1 of the
claim splits `amount` into the provider shares and ONE `RefundAttempt` of kind
`STORED_VALUE` (no charge, no provider); the provider shares run first and the
stored share only runs when every one of them was made (a declined card refund
leaves the cards and points alone); `OrderService._return_stored_value` then,
in one transaction under the order lock, credits the cards
(`GiftCardService.credit_refund(order, amount, refund_attempt=...)`) and
restores the points (`LoyaltyService.restore_spent_points`), settles the
attempt, derives `payment_status` and sends `order_stored_value_returned`. What
is still owed is read from the gift-card and points ledgers
(`PaymentLedger.stored_value` → `StoredValue`: REDEEM less REFUND_CREDIT per
card, spent less restored points, the order's loyalty discount in proportion to
the points not restored), never from a counter, and under the cards' and the
user's row locks, so a cancel and a refund (or two refunds) that race return
the value once: the one-credit-per-card-per-order unique constraint
(`unique_giftcard_refund_per_order`) is gone, because an order is credited back
in parts; the rows name the refund (`GiftCardTransaction.refund_attempt`,
`PointsTransaction.refund_attempt`).

**`payment_status` is REFUNDED only when the provider's money AND the stored
value are back** (`refund_status`), and PARTIALLY_REFUNDED before; an order
paid entirely with gift cards and points is refunded the same way (the dialog
is offered for it, `refund_open`). Rounding: the stored share is split to the
cent (cards) and the point (points), rounded down, so the LAST refund must ask
for what is left, the dialog's default; asking for the rounded figure can leave
a cent. A cancel still returns the whole redemption (`order_canceled`), and a
chargeback leaves the stored value where it is (owner decision). No refund
email is sent for a share that moved no money (the email is per REFUND row);
the order's history notes what went back and a REFUNDED order gets its
settlement credit document as usual.

**Confirming a bank transfer (`OrderService.confirm_offline_payment`, admin
action "Confirm payment received").** `payment_status` and `payment_id` are
read-only in the order form (a form save once marked orders paid with no charge
behind them), so money settled off the platform needs its own door. It is
offered only for a `PaySettlement.OFFLINE_TRANSFER` order still PENDING, for
exactly `amount_due` and with the bank's reference (required; the ledger key
`(bank_transfer, reference, OFFLINE)`, so one transfer cannot pay two orders).
Under the order lock it writes an `OFFLINE` ledger row
(`PaymentTransactionKind.OFFLINE_COLLECTION`, description "Confirmed by <email>: <note>"),
`mark_as_paid`, an `OrderHistory` PAYMENT entry (previous status, reference,
amount, `confirmed_by`, when = the row's time) and an admin log entry, and
moves a PENDING order to PROCESSING (which fires `order_paid`). Online
providers, courier cash and carrier-terminal orders are refused here: their
payments are recorded only by the provider's or courier's own confirmed event
(the #251 guards stand). The `OFFLINE` row counts as received money like a
CHARGE and, like courier cash, has no provider to refund it through: a refund
of it is paid back by hand and recorded with a repayment reference
(`is_cash_collection`).

**The staff action** (`OrderAdmin.refund_payment`, a dialog on the order's
detail page) is offered whenever money paid for the order can still be refunded
or a refund of it is unfinished (`PaymentLedger.refund_open`: the ledger holds
refundable money, or a refund attempt is still PENDING (even one that holds
everything that was left, so its retry or the way to Record refund outcome
stays reachable), or the order is paid through a registered provider whose
capture the ledger never recorded, which `refund_order` reads from the provider
once; decided without a provider call, so drawing the button costs nothing),
whatever the order's status: a return (DELIVERED, COMPLETED, RETURNED), a
goodwill refund, or a CANCELED order whose automatic refund was declined or
that was paid after the cancel (the dashboard's "Canceled orders to refund"
list is exactly what this action serves). It was limited to
`RETURN_HANDLING_STATUSES`, which left those canceled orders refundable only in
the provider's dashboard. Staff need the order's change permission. It is the
one **"Return and refund"** dialog (`ReturnOrderForm`, see "Refund by lines"
below): the lines that came back with their units, whether the shipping goes
back, a reason from the fixed list and an optional note, optionally the
restock, or, instead of lines, a goodwill amount. It calls `refund_order` and
reports the service's own refusal (nothing left, more than the remainder, not
paid through a provider, cash on delivery not collected yet) or its failure to
the staff member. Where the order holds cash a courier collected (a
`COD_COLLECTION` row with money left) the dialog also shows **"How it was paid
back"** (`repayment_reference`: the bank transfer id, say), and the form
requires it exactly when the amount asked reaches that cash
(`PaymentLedger.plan_refund` on the typed amount), so a refund inside a card
payment of a mixed order is not asked for one. It is the same action and the
same service call as a provider refund; see "Refunding cash on delivery" below.
A refund split across payments can be declined part-way, so the message
distinguishes three outcomes: all of it refunded,
`€50 of €60 refunded; the rest was declined: <reason>`, and "was not refunded;
see the order history". It writes an admin log entry whenever money moved, the
partial case included (who refunded what and why: the ledger row keeps the
reason, and a staff identity cannot be a foreign key into a tenant schema). It
changes the order status never, and the stock only when the staff member ticks
the restock option: moving the order to RETURNED / REFUNDED stays the "Mark as
..." actions, and restocking without a refund is the "Restock returned items"
action or `POST order-items/<id>/restock` (below). The read-only ledger is the
order page's "Payment Transactions" tab.

**Refund by lines** (`src/order/refund_lines.py`; its pieces are
`CreditAllocation` rows).
`refund_order(order, lines=[(item, units)], include_shipping=True, reason_code=..., reason=<note>)`
derives the amount from the figures the order froze, never from the catalogue:
a unit is worth its line's `price` x `quantity` less the line's
`discount_allocation` (`order.discounts.discounted_line_gross`) over the
quantity, at the line's own `vat_rate`. Units are priced cumulatively (the
first *k* units are worth `gross * k / n`, half-up to the cent, the last taking
the remainder), so a line refunded in several steps adds up to exactly its
value. Shipping is split over the rates the way the receipt splits it
(`order_vat_lines`, value apportionment), one piece per rate, and can be
refunded once; the payment fee is not a line option (a goodwill amount covers
it). An `amount` with lines is refused. Under the order lock the lines are
locked in pk order.

**The lines write their own allocation** ([§7.4](fiscal-mydata.md),
`OrderService._allocate_lines_claim`): the pieces (order line, kind goods or
shipping, VAT rate, units, exact gross) are laid over the refund's shares in
order, the provider's charges first, then gift cards and points
(`credit_allocation.split_over_shares`; a piece cut at a share becomes two rows
of the same line and rate, each with its own value and the request's units,
informational), and stored with the claim, under the order lock, before any
provider is called. Its credit document is then issued from those rows like
every other (`order.credit_documents`), so it carries exactly the lines' own
rates (returning one 13% item of a 24%/13% order credits 13% only), whatever
refunds came before or settle after. A bare `amount` stays a **goodwill
refund**: allocated pro rata over the headroom left at that moment, as before.

**What a line, and the shipping, has had back is measured in VALUE, never in
units** (`held_values`): the sum of the allocation rows of the line (the
shipping rows per VAT rate) of every source that holds room
(`CreditAllocation.objects.counting()`: settled, or still PENDING however old,
never declined; there is no timer), against the line's discounted gross. A
claim still PENDING holds its lines until it is resolved, a DECLINED one frees
them at once. The units still on offer are the ones that value has not covered
(`covered_units`), and the next units are priced from what is left, so a line
split over two charges (or a card and a gift card) whose second share was
declined still offers the unpaid part, never reads as fully refunded, and is
not restocked. `OrderItem.refunded_quantity` is a different count (units put
back on the shelf). A stored-value share's rows are scaled to what it really
returned once it settles (`credit_allocation.scale`, as for any stored-value
share), so what it did not return stays refundable; its points part owes no
credit, so each piece keeps only the gift-card part as room (`amount`) and the
rest as `CreditAllocation.points` (`credit_allocation.scale_pieces`): no room
on the receipt, no credit, never read by `headroom`, `counting()` or the credit
documents, but value the line HAS had back, so `held_values`
(`CreditAllocation.objects.holding()`) counts amount plus points and a line
refunded in full is never offered again. Settling the share scales the points
to what really came back (`credit_allocation.scale_points`).

**The lines must fit the headroom per rate at the claim**: every rate of the
pieces needs its own room (`credit_allocation.first_short_rate`), and a rate
the receipt no longer holds is refused with a clear message (a goodwill refund
of an amount can still be made) before any provider is called, as a bare amount
the headroom cannot hold is; when only a claim still PENDING keeps a rate short
(it would fit with the claim released: `credit_allocation.room_blockers`) the
refusal is the waiting one naming the blocking claims, for a goodwill amount as
for lines, never the final "holds only"; a source still undecided makes the
claim wait too (`credit_allocation.undecided`, `_waiting_error`).

**A duplicate payment is never taken out of the lines**: they are goods the
customer sent back, owed as credit at their own rates in full, so the claim's
`goods_claimed` equals its amount, it leaves `PaymentLedger.refundable_excess`
where it was, and the duplicate payment stays refundable as a bare amount
afterwards (the lines refund does not wait for a PENDING claim that holds
excess either, since it would take none).

**A request by lines is always planned fresh** from `held_values`; a declined
share holds nothing, so the next request quotes what is left of the line at the
current charges (`plan_refund`), and two requests for the same units never
share attempts. The only resume is **`retry_request_id`**: the `request_id` of
a refund by lines with live PENDING shares (a provider call whose outcome is
unknown) calls those shares again under their own idempotency keys, and claims
nothing else (no lines, shipping or amount may come with it). It resumes only
for a provider that honours idempotency keys
(`provider_supports(..., "idempotent_refunds")`) and never an overdue attempt
or a cash share (past `REFUND_RECONCILE_AFTER_MINUTES`: the provider may have
forgotten the key, so the reconciliation looks it up or staff record its
outcome); a bare retry (an amount) never resumes a lines refund (`request_id`
is set on its shares).
`OrderService.resumes_a_refund(order, amount, retry_request_id=..., by_lines=...)`
answers the same way without the lock (a request by lines that is no retry
never resumes anything), so the dialog asks no cash-payback reference of a
retry, and `OrderService.unresumable_reason` is the one rule the dialog uses to
offer the retry at all: for a provider that forgets keys, an overdue or a cash
share it shows the reason and points to Record refund outcome instead. It
judges only the request's shares still PENDING, the ones a retry would call: a
cash share already booked or declined beside a PENDING card share never blocks
the card retry. A line worth nothing (a free gift) counts no unit as covered
and can be returned for the shelf alone (the dialog, with the restock option,
writes no refund). The reason is a `RefundReason` (`RETURNED`, `DAMAGED`,
`WRONG_ITEM`, `NOT_AS_DESCRIBED`, `NOT_DELIVERED`, `DUPLICATE`, `GOODWILL`,
`OTHER`) kept in `RefundAttempt.reason_code`; `reason` is the note, and what
the ledger and the order history show is `"<category label>: <note>"` in the
staff member's language. Refunds made before the list keep their free text in
`reason` and an empty `reason_code`. Gift-card and points reversal are
unchanged: they follow the refunded VALUE (provider money first, the excess
onto the cards and points, §7.2 above), and the loyalty reversal of earned
points still waits for the order to be fully returned, so refunding lines moves
them exactly as a bare amount of the same value would.

**The "Return and refund" dialog** replaces the old separate "Refund (amount,
reason)" action. It shows one units field per line with units not yet refunded
(its help text gives the units still to refund, the value left to refund on the
line and its VAT rate), a shipping switch when the order charged shipping that
is not yet refunded, an optional restock switch (offered when the order is
DELIVERED, COMPLETED or RETURNED and the user may change order lines), the
reason list, a note, and the goodwill amount (not together with lines). When a
refund by lines has a live PENDING share (a provider call with an unknown
outcome), the dialog offers **"Retry the pending refund"** (`pending_request`):
on its own, without lines, shipping or amount, it resumes that request through
`refund_order` under the SAME idempotency keys, never as a new goodwill claim
(which could pay its lines twice). The restock runs after the refund and only
when every share was made (`OrderAdmin._restock`, the same code as the "Restock
returned items" action, which stays for returns that move no money, such as an
uncollected COD parcel). The dialog cannot total the lines live; the amount is
worked out from the frozen figures when the refund is claimed and reported when
it is made.

**`POST /api/v1/order-items/<id>/restock`** (staff; it was `.../refund`) puts
returned units of one line back in stock and records them on the line
(`refunded_quantity`, `is_refunded`). It moves NO money and says so: what the
customer is paid back is decided at order level, because a line's snapshot
price is not what was paid for it (tax, shared discounts, shipping). It used to
answer "Refund processed successfully" with a `refunded_amount` for money that
never moved.

**There is one restock path:
`OrderItem.refund(quantity, performed_by=, reason=)`.** The endpoint and the
order page's **"Restock returned items"** action
(`OrderAdmin.restock_returned`, a dialog with one quantity per line that still
has units not put back, and a reason) both call it, so the guards live in the
method and hold for both. Under the order's row lock, then the line's (always
in that order, so the two paths cannot deadlock): the order must be DELIVERED,
COMPLETED or RETURNED; the quantity must be positive and no more than
`quantity - refunded_quantity`, so a unit is never put back twice and never
more than were sold (the DB check constraint is the backstop); the stock goes
through `StockManager.increment_stock`, which locks the product row (a variant
is its own product, so each line restocks its own) and writes a `StockLog` row
with the staff member (`performed_by`), the time and the reason. The admin
action wraps all lines in one transaction (all-or-nothing), writes an admin log
entry, needs the change permission on order lines, and is offered only while a
line has units left on an order in those statuses. Nothing here touches
payments; after a refund the admin points staff at the restock action but never
does it unasked, because returned goods may be damaged.

**Refunding cash on delivery.** A courier's collection is a `COD_COLLECTION`
row ([§4.4](payments.md)), and the ledger counts it as money received:
`captured`, `balances`, `refundable` and `plan_refund` read CHARGE and
COD_COLLECTION rows alike, so `refund_status` (refunded ≥ received) works for a
COD order as for a card one. Before this, a refund of a collected COD order was
refused (`This order was not paid through a payment provider...`): `captured`
and `balances` read CHARGE rows only, so `refundable` was 0 and no REFUND row,
no status change and no myDATA credit note could follow the bank transfer the
merchant had already made. There is still no provider to call for cash, so
`refund_order(..., repayment_reference=...)` records the share the merchant
paid back themselves:

- A plan share against a `COD_COLLECTION` is written with
  `PaymentLedger.record_cod_refund`: a REFUND row whose `charge` is the
  collection, `provider="manual"` (`MANUAL_REFUND_PROVIDER`, who moved the
  money) and
  `provider_reference = f"order-{order.pk}-{collection.pk}-{reference}"`.
  Nothing is sent anywhere; the staff member's word is the evidence.
- `repayment_reference` is **required exactly when the plan touches such
  cash**, and is validated before the first provider call (a mixed order is
  never refunded half): blank is refused (`Say how the cash was paid back...`),
  so is one longer than `REPAYMENT_REFERENCE_MAX_LENGTH` (100). The admin form
  checks the same rule up front and removes the field when the order has no
  cash left to refund. A reference given for a refund that touches only
  provider payments is ignored.
- **Idempotent**: the reference is scoped to the order and the collection in
  the ledger key, and the unique constraint on
  `(provider, provider_reference, kind)` is what keeps it from being booked
  twice. The service refuses a reference already used on that collection
  (`A refund with this reference is already recorded for this order.`) before
  writing anything, so a double submit books one refund and the second answer
  is that message. The same transfer id on another order is its own refund. Two
  parts of one COD order need two references.
- One REFUND row, one `order_refunded` per share, so the 11.4 credit note
  (`create_pending_credits` reads every REFUND row of the order, whoever moved
  it), the refund email, loyalty and Meta CAPI run exactly as for a Stripe or
  Viva refund. `payment_status` is derived from the ledger by `record_refund`.
- Under the same `select_for_update` lock as every refund, never more than
  `PaymentLedger.refundable`.
- An order whose cash was not collected yet holds no ledger row, so
  `refundable` is 0 and the error says
  `The payment on delivery has not been collected yet, so there is nothing to refund.`
  (a COD or carrier-terminal pay way with a payment status that is not paid).
  The cancel path (`cancel_order`) passes no reference and only runs for an
  order with a `payment_id`, which a COD order does not have; a mixed order
  canceled that way would be refused whole, before any provider call, and
  report `refund_info["refunded"] = False` (staff then use the Refund action).
