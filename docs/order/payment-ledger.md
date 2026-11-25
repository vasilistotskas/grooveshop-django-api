# Order system: the payment ledger

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7.3 The payment ledger (`PaymentTransaction`)

`src/order/models/payment_transaction.py`, written only through
`src/order/payments/payment_ledger.py` (`PaymentLedger`). Append-only, like
`GiftCardTransaction`: a row is never deleted and, but for its provider `fee`
(below), never updated, and `Order` → ledger is `PROTECT` (deleting an order
for real has to remove its ledger first, as the demo reset does).

| Column | Meaning |
|---|---|
| `order` | the order (`payment_transactions`) |
| `kind` | `CHARGE` (a provider captured money), `REFUND` (a provider paid it back, or the merchant paid cash back themselves), `CHARGEBACK` (a lost dispute took the money back: written when it closes against the merchant, never when it opens; it is not a refund and moves no `payment_status`), `COD_COLLECTION` (a courier collected cash), `REFUND_REVERSAL` (a refund the ledger counted that the provider later reported FAILED: the money counts as held again, [§4.2](payments.md); it names the charge like a refund and is keyed on the provider's refund id). |
| `amount` | always positive; the kind gives the direction |
| `provider` | `stripe`, `viva_wallet`, or the courier's `acs_cod` / `boxnow_cod`; `manual` on a REFUND the merchant made themselves (cash on delivery paid back by bank transfer or in cash) |
| `provider_reference` | the provider's own id: PaymentIntent, Viva transaction, refund id, voucher / parcel (a Stripe refund, whoever made it, is keyed on its refund id `re_...`) |
| `charge` | on a REFUND or a CHARGEBACK, the CHARGE it reverses (an order paid twice is refunded payment by payment); null otherwise (a DB check enforces both) |
| `description` | the reason given for a refund |
| `fee`, `fee_source` | what the provider charged for this movement, as the provider reported it, and which report (`PaymentFeeSource`: `STRIPE_BALANCE_TRANSACTION`, `VIVA_TRANSACTION_PRICE`). (continues under the table) |
| `created_at` | when it was recorded |

**`fee`, `fee_source`.** Both empty until reported: **null is "not reported",
never zero, never an estimate**. The only column written after the row exists
(`PaymentLedger.record_fee`, replacing an earlier figure); a CHECK keeps a fee
and its source together and the fee non-negative. Only the OWNER's profit
report reads it (`docs/profit-report.md`)

`UNIQUE (provider, provider_reference, kind)` is the idempotency of every
writer: a delivery repeated by the provider records nothing the second time
(`record_*` return `created=False`). Writers (all under the order row lock):
`OrderService.handle_payment_succeeded`, `handle_stripe_checkout_completed`,
`viva_settlement.handle_payment_created` (CHARGE); `OrderService.settle_cod_payment`, reached
from `AcsService._mark_cod_order_paid_if_pending` and
`BoxNowService._mark_cod_order_paid_on_delivery` (COD_COLLECTION, written in
the same atomic step as the payment status, [§4.4](payments.md));
`RefundService.refund_order`, `handle_stripe_refund_made`,
`_handle_reversal_created` (REFUND, booked by `RefundService.book_refund`,
which writes the row through `record_refund` and then settles the attempt it
fulfils, decides the order's allocation and re-derives the overcharge flag:
the ledger itself imports no allocation code);
`handle_stripe_dispute_closed` (CHARGEBACK, through `record_chargeback`). The
Viva handlers (`_handle_account_transaction_event`, and
`_handle_reversal_created` for a refund-claim type) book it through the same
`record_chargeback`.

**`RefundNotification`** (`src/order/models/refund_notification.py`) is the
claim on a refund's confirmation email: one row per REFUND row (a one-to-one,
so the database arbitrates between two tasks), inserted before the email is
sent and deleted when the send fails for good. It is a side table, not a column
on the ledger, so the ledger's rows stay the money facts they are and are never
updated; who has been told about a refund is delivery bookkeeping with a
lifecycle of its own. It follows its ledger row on delete (`CASCADE`).

Everything else is read from the sums: `PaymentLedger.captured` (money
received: CHARGE and COD_COLLECTION), `refunded` (REFUND, by a provider or by
the merchant, less REFUND_REVERSAL), `charged_back` (CHARGEBACK),
`fully_returned` (refunded plus charged back covers captured, what the loyalty
reversal asks), `refundable` (per payment, collected cash included, less what a
chargeback took and what live refund attempts hold), and
**`refund_status(order)`, the one function that decides REFUNDED versus
PARTIALLY_REFUNDED** (refunded plus charged back ≥ captured; chargebacks count,
so a chargeback before the last refund never leaves the order
PARTIALLY_REFUNDED with nothing refundable and the gift card unreleased; and
COMPLETED again when every refund was reversed). `record_refund` writes the
derived status onto the order in the same step as the row, so no path decides
it for itself and it cannot go backwards. `payment_status` stays a column
(every filter, serializer and guard reads it); the ledger is what it is derived
from after a refund.

**An order paid twice is flagged, never silently booked**
(`PaymentLedger.sync_overcharge`, called by `_record` for every new row, under
the order lock). `Order.overcharged_at` is set while `held` (captured, less
refunded, less charged back) exceeds what the order is for
(`calculate_order_total_amount()`, the one figure every payment path charges
and compares a capture with; not the `amount_due` copy) by more than `PAYMENT_AMOUNT_TOLERANCE`, and
cleared when a refund, a reversal or a chargeback changes the sums so it no
longer does. On the change from clear to set it logs ERROR, notes the order and
sends `ops.overcharged` once; a row the provider redelivers is not new and
never reaches it, and a figure that stays over changes nothing. Both CHARGE
rows stay (the money moved), the order keeps its FIRST `payment_id` (Viva
`viva_settlement.handle_payment_created` and Stripe `checkout.session.completed` stop after
booking a second payment on an order another one already paid, so there is no
second confirmation email either), and nothing is refunded automatically: which
payment to give back is a person's call. A partial refund of a normal order
never flags; a charge on a canceled order is `ops.payment_after_cancel`'s case
(it equals the amount due). Staff see it as the "Orders paid twice" dashboard
row, the order list filter `?overcharged=yes` and the read-only
`Overcharged at` field. `payment_intent.succeeded` finds the order by
`payment_id`; an intent the order does not name (a second payment, made
through a link the order no longer records) is found by the binding metadata
the server wrote on it (`OrderService._bound_order_uuid`: purpose `order`, this
order's uuid, this tenant, checked by `binding_mismatch`), so it is booked and
flagged like any second payment, and the first `payment_id` is kept. The refund, failed-refund and dispute events
(`charge.refunded`, `refund.failed`, `charge.dispute.created`,
`charge.dispute.closed`) find the order through the ledger's Stripe CHARGE row
for the intent (`_locked_order_for_stripe_intent`), not `payment_id`, so an
event on the second intent reaches the ledger and moves the flag; an order
whose capture the ledger never recorded has no row and is found by `payment_id`.

**What the customer owes is not what was collected.** `Order.amount_due`
(`amountDue` in the API) is what the order costs the customer:
`calculate_order_total_amount()` (lines and extras, less promotion, loyalty and
gift card), written at creation and again where a deduction changes (the
standalone loyalty redemption). It says nothing about money collected, which is
the ledger's CHARGE and COD_COLLECTION rows.

What is still to be paid is **`Order.remaining_amount`**
(`pricingBreakdown.remainingAmount`): `amount_due` while the payment is not
settled, and 0 once `payment_status` is in `SETTLED_PAYMENT_STATUSES`. It is
read from the status and never from the ledger, because a settled order can
hold no ledger row: a payment staff recorded by hand, cash collected under a
`payment_method` the 0067 backfill does not key on, and the demo tenant's
seeded orders would all read as unpaid on a ledger sum. So a PENDING /
PROCESSING / FAILED online order owes its whole `amount_due` (the storefront's
"pay €X" state), a COD order owes it until the courier collects (it is also the
amount the courier is told to collect: `AcsShipment.cod_amount`,
`BoxNowShipment.amount_to_be_collected`), and a COMPLETED order owes 0. A
refund never creates an amount owed (PARTIALLY_REFUNDED and REFUNDED stay at
0), and a CANCELED order owes 0 (it closed unpaid, so no payment will be
collected: `PAYMENT_CLOSING_STATUSES`).

Whether an order is PAID is `payment_status` alone (`Order.is_paid`, `isPaid`,
and the `is_paid` order filter: `true` is COMPLETED, `false` is every other
status). The amount due never decides it: an unpaid order owes money too, and a
paid one can owe 0.00 when deductions covered the whole total.

Refunds are ledger rows only, and a provider refunds the amount it is given.

**Orders older than the ledger.** Migration
`0067_backfill_payment_transactions` gave them rows from what the order rows
prove and nothing it would guess: CHARGE for Stripe and Viva orders (the amount
checkout verified against the provider), COD_COLLECTION for `acs_cod` /
`boxnow_cod` orders, and REFUND for a recorded refund that names a provider
refund id and an amount. So such an order may lack what was never recorded: the
amount the provider actually captured, any payment but the last of an order
paid twice under two Viva codes, a COD collection's courier reference (the row
is keyed `order-<id>`), who refunded, and a refund no provider id was kept for
(an in-app Viva refund the provider never made has its CHARGE and no REFUND, so
the ledger shows the payment refundable, which is true). A payment the ledger
lacks is read from the provider when a refund first needs it
(`PaymentLedger.ensure_charge`). `payment_status` was left alone.
