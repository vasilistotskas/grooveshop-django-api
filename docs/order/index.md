# Order / Payment / Shipping / Notification System

**Reference for anyone (Claude included) maintaining this surface.**
Keep these files synchronised when invariants change. Cross-references
are file paths + line numbers; the system has enough load-bearing
"don't undo this" pieces that drift here is expensive.

## Which file to read

This is one reference in several files, so each fits in one read. A
section keeps its number (§), and each number lives in one file: "§7.3"
is `payment-ledger.md`. Read the file for what you are changing, then
`invariants.md`.

| § | File | Read it before changing |
|---|---|---|
| 1 | [`overview.md`](overview.md) | anything here: the three axes of an order (status, payment status, settlement), the two "payment method" columns, the carrier adapter |
| 2 | [`state-machines.md`](state-machines.md) | an order status or payment status transition, who may make it, the follow-up flag |
| 3, 3.0 to 3.2 | [`creation.md`](creation.md) | checkout: the checkout quote (the one total) and the expected total, the one creation path (every pay way order first), repeats and agent checkouts, locks and stock holds |
| 3.3 | [`creation-address.md`](creation-address.md) | delivery-address rules: the country's postcode and street rules, regions, pickup-point orders delivered to the point's address |
| 3.4 to 3.7 | [`creation-records.md`](creation-records.md) | what an order stores when it is placed: acquisition source, VAT rate per line, legal acceptance, the line's name and SKU |
| 4 | [`payments.md`](payments.md) | Stripe or cash on delivery, and what every provider shares: per-tenant credentials, webhooks, fees, provider refunds, disputes |
| 4.3 | [`payments-viva.md`](payments-viva.md) | Viva Wallet: settlement and its checks, the webhook events (payment, refund, failure, fee), the return page, a lost webhook read back from Viva |
| 5, 5.0, 5.3, 5.4 | [`shipping.md`](shipping.md) | the rules every carrier shares (the lock order included), shipping prices (`ShippingRate`), adding a carrier, operational alert emails |
| 5.1 | [`shipping-acs.md`](shipping-acs.md) | `src/shipping_acs/`: vouchers, the voucher hold, pickup lists, tracking polls, COD payouts, the concurrency cap |
| 5.2 | [`shipping-boxnow.md`](shipping-boxnow.md) | `src/shipping_boxnow/`: parcels, the webhook, the cancel rule, compartment sizes |
| 6 | [`emails-and-notifications.md`](emails-and-notifications.md) | an order email or a live toast: triggers, locale, suppression, marketing unsubscribe, account emails |
| 7, 7.1, 7.1.0 to 7.1.2 | [`cancel.md`](cancel.md) | cancelling an order: every path, the payment hold and its sweep, asking the courier first (a customer) or after the commit (staff) |
| 7.2, first half | [`refunds.md`](refunds.md) | what a refund returns: the paths, value-based refunds, refund by lines, restock, cash on delivery |
| 7.2, second half | [`refund-execution.md`](refund-execution.md) | how `refund_order` runs: the plan from the ledger, attempts and idempotency keys, reconciliation, provider statuses, the `order_refunded` contract |
| 7.3 | [`payment-ledger.md`](payment-ledger.md) | `PaymentTransaction` and `PaymentLedger`: recording money, what is refundable, `amount_due`, an order paid twice |
| 7.4 | [`fiscal-mydata.md`](fiscal-mydata.md) | myDATA: the receipt at shipment, credit documents, who may issue, the settings |
| 7.4, movement | [`fiscal-movement-receipts.md`](fiscal-movement-receipts.md) | a courier or locker sale's receipt: issued when the carrier holds the parcel, its movement block, an unanswered send, an order canceled before the pickup (canceled in myDATA, credited, or held for staff) |
| 7.4, VAT split | [`fiscal-credit-vat-split.md`](fiscal-credit-vat-split.md) | how a credit document is split over VAT rates (`CreditAllocation`), decided and undecided claims |
| 8 | [`invariants.md`](invariants.md) | anything: the rules that must not be undone, each with where it is explained |
| 9 | [`playbook.md`](playbook.md) | step lists: a new order email, a new status, a new API field, after a manual fix in production, a wrong email subject |

Keep each file well under 40,000 characters and its lines short: wrap
prose, and put a paragraph under its table, never in a cell. A file that
outgrows the limit is split on a subject, and this table gains a row.

Last refresh: 2026-11-24 (every pay way creates the order first and pays
it afterwards, [§3.1](creation.md); before
that 2026-11-21: an order's address line 2, [§3.3](creation-address.md);
an order has one payable payment link: a request for one returns the live
one, [§4](payments.md); the cart digest leaves out the pay way and
is returned once a delivery is chosen, [§3.0](creation.md); the quote's `cartDigest`: an order created
with `expectedCartDigest` is made of what was quoted, [§3.0](creation.md);
`expectedTotal` is required on order creation, [§3.0](creation.md);
order creation takes one street line and splits its number,
[§3.3](creation-address.md); a pickup-point order is delivered to the point's
address and needs none of the buyer's, [§3.3](creation-address.md); before that
2026-11-18: an agent checkout places one order, and a repeat
of its creation is answered with that order, [§3.2](creation.md); one
checkout total: `OrderQuoteService.quote`
behind `POST /cart/quote` and order creation, and
an order created with `expectedTotal` exists only at that total,
[§3.0](creation.md); before that 2026-11-16: a staff or system cancel asks the courier after it
has committed, with no row locked, and a sweep finishes a cancel whose worker
stopped in between, [§7.1.2](cancel.md); one lock order for an order and its
shipment, the order first, [§5](shipping.md); before that 2026-11-14: an agent
order is refused while its cart's earlier
order awaits its online payment, [§3.2](creation.md); before that 2026-11-11:
a BoxNow `P410` on creation takes over the parcel
BoxNow holds under the `orderNumber`, and a cancel of a shipment with no
parcel number whose creation was sent looks it up and cancels it there,
[§5.2](shipping-boxnow.md) / [§7.1](cancel.md); before that 2026-11-10: a
BoxNow `canceled` parcel event no longer cancels
the order: it records the shipment CANCELED and an open order behind it needs
follow-up, [§2.1](state-machines.md) / [§2.3](state-machines.md) /
[§5.2](shipping-boxnow.md) / [§7.1](cancel.md); a repeat cancel of a canceled
BoxNow shipment changes nothing, [§5.2](shipping-boxnow.md); before that
2026-11-09: CANCELED is reached only through `cancel_order`:
`update_order_status` and the `update_status` endpoint refuse it, and the
`order_canceled` voucher safety net is gone, [§2.1](state-machines.md) /
[§7.1](cancel.md); before that 2026-10-29: the order emails' subject and
context are built once in `src/order/emails.py` for the sender and the admin
preview, [§6.1](emails-and-notifications.md); marketing mail honours the
account-level opt-out the one-click link records,
[§6.5](emails-and-notifications.md); the store's contact/feedback notifications
and the allauth account emails, [§6.6](emails-and-notifications.md) /
[§6.7](emails-and-notifications.md); before that 2026-10-18: value-based
refunds and the negative-balance setting, [§7.2](refunds.md); refunds claim,
call and record with no transaction open across the provider, `RefundAttempt`,
Stripe `refund.failed` reversal, [§4.2.0](payments.md) /
[§7.2](refund-execution.md); before that 2026-10-13: refunding collected cash
on delivery, [§7.2](refunds.md); before that 2026-10-12: the myDATA retail
document lifecycle: a receipt at shipment, a credit document per refund, no
platform invoices, [§7.4](fiscal-mydata.md); before that the payment ledger:
partial refunds, [§7.2](refunds.md) / [§7.3](payment-ledger.md); before that
2026-09-30: every payment start refuses a non-PENDING order, a change during
the provider call wins with 409, webhook history/emails only for an applied
event, BoxNow webhook 400 for a non-object, non-UTF-8 or too-deep body; before
that: order acquisition source, admin payment-method column, then no mint
before payment, payment-after-cancel ahead of the settled guard, admin status
read-only, ACS pickup-list `blocked_unprinted`, postcode refusal). See git log
for changes since.
