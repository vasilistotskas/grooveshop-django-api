# Order system: the payment ledger

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7.3 The payment ledger (`PaymentTransaction`)

`src/order/models/payment_transaction.py`, written only through
`src/order/payment_ledger.py` (`PaymentLedger`). Append-only, like
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
`_handle_payment_created` (CHARGE); `OrderService.settle_cod_payment`, reached
from `AcsService._mark_cod_order_paid_if_pending` and
`BoxNowService._mark_cod_order_paid_on_delivery` (COD_COLLECTION, written in
the same atomic step as the payment status, [§4.4](payments.md));
`OrderService.refund_order`, `handle_stripe_refund_made`,
`_handle_reversal_created` (REFUND, through `record_refund`);
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
so a chargeback before the last refund no longer leaves the order
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
and compares a capture with; not the `amount_due` copy, which an order from the
previous release can carry as 0) by more than `PAYMENT_AMOUNT_TOLERANCE`, and
cleared when a refund, a reversal or a chargeback changes the sums so it no
longer does. On the change from clear to set it logs ERROR, notes the order and
sends `ops.overcharged` once; a row the provider redelivers is not new and
never reaches it, and a figure that stays over changes nothing. Both CHARGE
rows stay (the money moved), the order keeps its FIRST `payment_id` (Viva
`_handle_payment_created` and Stripe `checkout.session.completed` stop after
booking a second payment on an order another one already paid, so there is no
second confirmation email either), and nothing is refunded automatically: which
payment to give back is a person's call. A partial refund of a normal order
never flags; a charge on a canceled order is `ops.payment_after_cancel`'s case
(it equals the amount due). Staff see it as the "Orders paid twice" dashboard
row, the order list filter `?overcharged=yes` and the read-only
`Overcharged at` field. `payment_intent.succeeded` finds the order by
`payment_id`; an intent the order no longer names (a second tab, or an older
one paid after a retry opened a newer intent) is found by the binding metadata
the server wrote on it (`OrderService._bound_order_uuid`: purpose `order`, this
order's uuid, this tenant, checked by `binding_mismatch`), so it is booked and
flagged like any second payment, and the first `payment_id` is kept. A
payment-first intent is bound to a cart (its order may not exist yet) and "not
found" stays the answer for it. The refund, failed-refund and dispute events
(`charge.refunded`, `refund.failed`, `charge.dispute.created`,
`charge.dispute.closed`) find the order through the ledger's Stripe CHARGE row
for the intent (`_locked_order_for_stripe_intent`), not `payment_id`, so an
event on the second intent reaches the ledger and moves the flag; an order
whose capture the ledger never recorded (paid while the previous release
served) has no row and is found by `payment_id`.

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

**`Order.metadata["refunds"]` is gone**, and with it the Stripe refund
`webhook_processed_*` flag and `PaymentStatus.after_refund`.
`PaymentProvider.captured_amount` is gone too: providers refund an amount they
are given.

**Migration `0067_backfill_payment_transactions`** wrote rows for the orders
paid before the ledger existed, from what the rows prove and nothing it would
guess: CHARGE (amount `paid_amount`, the column `Order.amount_due` was called
until `0070`, which checkout verified against the provider) for Stripe and Viva
orders, COD_COLLECTION for `acs_cod` / `boxnow_cod` orders, REFUND for
`metadata["refunds"]` entries that name a provider refund id and an amount (the
old `"full"` marker means whatever the payment still held; a Stripe
`charge.refunded` entry's cumulative total becomes one row for the part the
named refunds do not cover). It could not reconstruct: the amount the provider
actually captured (it uses `paid_amount`); any payment but the last of an order
paid twice under two Viva codes (`payment_id` holds only the latest); a COD
collection's courier reference (the row is keyed `order-<id>`); who refunded (a
staff identity cannot be a foreign key into a tenant schema); a refund no
provider id was kept for (the four production Viva orders refunded "in-app"
before #134 show REFUNDED but the money never left Viva: they get their CHARGE
and no REFUND, so the ledger shows the payment refundable, which is true); and
anything pods of the previous release wrote while it ran (a payment recorded in
that window is read from the provider when a refund first needs it).
`payment_status` was left alone.

**Migrations `0070_order_amount_due` and
`0071_order_drop_paid_amount_columns`** rename `Order.paid_amount` to
`amount_due` over two releases (`docs/migrations.md`). `0070`, the expand step,
adds `amount_due` NOT NULL with a `db_default` and its currency column, copies
`paid_amount` into them, and takes `paid_amount` and `paid_amount_currency` out
of Django's state only; both stay in the database, nullable, so the new code's
INSERTs (which no longer name them) work and the release still serving can keep
writing them. django-money builds the currency column itself and cannot give it
a `db_default`, so it is added with its own
`ALTER TABLE ... ADD COLUMN ... DEFAULT 'EUR'` and declared in state
separately. `0071`, the contract step, ships once the storefront and the
gateway read `amountDue`. It declares
`contract_of = [("order", "0070_order_amount_due")]`, re-copies `paid_amount`
into `amount_due` where `amount_due = 0 AND paid_amount > 0` (the orders the
previous release created while it still served, which left `amount_due` at its
default) and drops both columns; its reverse re-adds them nullable, without
values. **The API expanded the same way**: the `0070` release emitted
`paidAmount` (top level and in `pricingBreakdown`) beside `amountDue`, both
`deprecated` in the schema, because the storefront's and the gateway's
generated schemas required `paidAmount` and removing it in the release that
added `amountDue` would have failed every order response parsed between the two
rollouts. The `0071` release removed it with the columns
(`OrderSerializer.paid_amount`, the `paid_amount` key of
`get_pricing_breakdown` and its schema), so `paidAmount` is no longer anywhere
in the API; the `paidAmount*` filters and ordering went with `0070`.
