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
| 3, 3.1 to 3.3 | [`creation.md`](creation.md) | checkout: the payment-first and order-first creation paths, locks and stock holds, delivery-address rules |
| 3.4 to 3.7 | [`creation-records.md`](creation-records.md) | what an order stores when it is placed: acquisition source, VAT rate per line, legal acceptance, the line's name and SKU |
| 4 | [`payments.md`](payments.md) | Stripe, Viva Wallet or cash on delivery: per-tenant credentials, webhooks, fees, provider refunds, disputes |
| 5, 5.0, 5.3, 5.4 | [`shipping.md`](shipping.md) | the rules every carrier shares, shipping prices (`ShippingRate`), adding a carrier, operational alert emails |
| 5.1 | [`shipping-acs.md`](shipping-acs.md) | `src/shipping_acs/`: vouchers, the voucher hold, pickup lists, tracking polls, COD payouts, the concurrency cap |
| 5.2 | [`shipping-boxnow.md`](shipping-boxnow.md) | `src/shipping_boxnow/`: parcels, the webhook, the cancel rule, compartment sizes |
| 6 | [`emails-and-notifications.md`](emails-and-notifications.md) | an order email or a live toast: triggers, locale, suppression, marketing unsubscribe, account emails |
| 7, 7.1, 7.1.0, 7.1.1 | [`cancel.md`](cancel.md) | cancelling an order: every path, the payment hold and its sweep, asking the courier first |
| 7.2, first half | [`refunds.md`](refunds.md) | what a refund returns: the paths, value-based refunds, refund by lines, restock, cash on delivery |
| 7.2, second half | [`refund-execution.md`](refund-execution.md) | how `refund_order` runs: the plan from the ledger, attempts and idempotency keys, reconciliation, provider statuses, the `order_refunded` contract |
| 7.3 | [`payment-ledger.md`](payment-ledger.md) | `PaymentTransaction` and `PaymentLedger`: recording money, what is refundable, `amount_due`, an order paid twice |
| 7.4 | [`fiscal-mydata.md`](fiscal-mydata.md) | myDATA: the receipt at shipment, credit documents, who may issue, the settings |
| 7.4, VAT split | [`fiscal-credit-vat-split.md`](fiscal-credit-vat-split.md) | how a credit document is split over VAT rates (`CreditAllocation`), decided and undecided claims |
| 8 | [`invariants.md`](invariants.md) | anything: the rules that must not be undone, each with where it is explained |
| 9 | [`playbook.md`](playbook.md) | step lists: a new order email, a new status, a new API field, after a manual fix in production, a wrong email subject |

Keep each file well under 40,000 characters and its lines short: wrap
prose, and put a paragraph under its table, never in a cell. A file that
outgrows the limit is split on a subject, and this table gains a row.

Last refresh: 2026-11-10 (a BoxNow `canceled` parcel event no longer cancels
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
