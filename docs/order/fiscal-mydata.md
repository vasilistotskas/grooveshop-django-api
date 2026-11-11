# Order system: retail tax documents (myDATA)

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7.4 Retail tax documents (myDATA): a receipt at shipment, a credit per refund

`src/order/mydata/` (builder, client, parser, service),
`src/order/invoicing.py`, `src/order/credit_documents.py`,
`src/order/models/invoice.py` and `retail_credit.py`. The platform issues
**retail** documents only, over the ERP `SendInvoices` channel, with the
tenant's own credentials (`tenant.credentials.mydata_credentials()`):

- **11.1 retail receipt** (ΕΛΠ art. 12(1)): the order's `Invoice` row. The
  model keeps its name from when it also carried B2B invoices; it is the
  receipt now.
- **11.4 retail credit document** (art. 12(4)): a `RetailCredit` row. AADE
  forbids `correlatedInvoices` on 11.4 (error 205, confirmed against the dev
  API on 2026-10-12), so the payload names no receipt and
  `specialInvoiceCategory` 13 ("Δυσχέρεια Συσχέτισης", optional and valid only
  for 11.4/14.30) is not sent; the receipt-to-credit link exists only in
  `RetailCredit.invoice`, the admin and the credit PDF ("Credits receipt X
  (MARK)").

Both are classified `E3_561_003` + `category1_1` ("Έσοδα από Πώληση
Εμπορευμάτων"; `category1_3` is services), a pair AADE's combinations sheets
for 11.1 and 11.4 allow.

**Who may issue over this channel.** Retail receipts are issued through a
fiscal device (ΦΗΜ) or a licensed e-invoicing provider (ν.4308/2014 art. 12 §8,
§10); AADE: «δεν μπορεί το ERP να αντικαταστήσει την υποχρέωση χρήσης ΦΗΜ. Μόνο
η επιλογή πιστοποιημένου Παρόχου» (myDATA FAQ 23/5/2022, Q37). The ERP channel
is only for a business AADE exempts from the ΦΗΜ (ΠΟΛ.1002/2014; online sales
are not exempt as such), and since ν.5222/2025 art. 238 (28/7/2025) even exempt
businesses are pointed to timologio or a provider. So the default — and every
store that takes real orders today — is that the store issues its receipts and
credits outside the platform, and the platform issues nothing. A
licensed-provider integration is a parked proposal (issue #197).

**The AADE sandbox is the exception.** Documents sent to
`MYDATA_ENVIRONMENT=dev` (`mydataapidev.aade.gr`, "για τη φάση της ανάπτυξης
και διενέργειας δοκιμών" in the myDATA REST API documentation) are test
registrations, not tax documents. A store that takes no real orders may issue
there without a recorded ground: a demo store (`Tenant.is_demo`), or any store
outside the production deployment (`settings.REAL_ORDERS_DEPLOYMENT` is false
only for `SYSTEM_ENV` `staging`, `dev` and `ci`, so an unknown deployment name
fails closed). `MyDataConfig.issues_to_sandbox_only` is that condition. A real
store in production needs the ground in the sandbox too: the setting defaults
to `dev`, so exempting the sandbox everywhere would let any store that switches
myDATA on email sandbox "receipts" to its customers.

**The demo store's specimen receipt.** A demo store (`Tenant.is_demo`, read
through `bound_tenant()`) cannot show a customer a real receipt: it holds no
ΦΗΜ ground, so a platform receipt exists only when it issues to the AADE
sandbox, and a sandbox "receipt" is a test registration. Instead
`GET /order/{id}/specimen/download` renders, on demand, the same
`invoices/invoice.html` through the same `_build_context` and
`_render_pdf_bytes` as the real receipt, from an unsaved `Invoice` and the live
order, with a «ΔΕΙΓΜΑ» watermark (a `position: fixed` box, which WeasyPrint
repeats on every page), the title line "SPECIMEN - not a tax document", and no
QR code (nothing is registered, so nothing to verify).
`order.invoicing.specimen_available` is the one gate, used by the endpoint and
by `OrderDetail.hasSpecimenReceipt`: a demo store AND `receipt_issuable(order)`
(a RECEIPT order at SHIPPED onwards, `RECEIPT_ISSUING_STATUSES`); any other
store and any earlier status answer 404 and `false`. It never issues: no
`Invoice` row, no `InvoiceCounter` slot, no stored file, no myDATA call,
nothing emailed; the PDF is streamed and forgotten. The API only exposes the
flag and the download; the storefront button, when it lands, is to sit beside
(never instead of) the real receipt's `hasInvoice`. Access is the same as the
receipt's: the owner, staff, or a guest holding the order's `?uuid=`
(`/invoice`, `/invoice/download` and the specimen route are all in
`GUEST_UUID_QUERY_ACTIONS`; a uuid never opens an account-owned order, and the
metadata's `downloadUrl` carries the uuid for a guest order). Each call is a
WeasyPrint render on the request worker, so the route carries its own
fail-closed throttle (`OrderSpecimenThrottle`, scope `order_specimen`,
10/minute per user or IP; the shared demo logins pool into one bucket). Pinned
by `tests/integration/order/test_specimen_receipt.py`.

**Settings.** `MYDATA_ENABLED` is the master switch, and
`MYDATA_FHM_EXEMPTION_GROUND` records the legal ground of the store's ΦΗΜ
exemption (the ΠΟΛ.1002/2014 provision, the accountant who confirmed it, the
date). `MyDataConfig.is_ready()` needs both (the ground unless
`issues_to_sandbox_only`), a known `MYDATA_ENVIRONMENT` (anything but `dev` or
`prod` is refused, never guessed), plus the tenant's credentials; until then
nothing is built or sent, whoever asks: `generate_invoice` refuses to issue
(`ReceiptNotIssuable`), the order's "Invoicing & myDATA" actions are hidden and
refused (`has_retail_documents_permission`), the order's Receipt card says the
store issues the receipt outside the platform, and the dashboard shows a danger
alert when the switch is on but the ground is blank (outside the sandbox
exception), or when the environment is invalid. "Regenerate receipt" only
re-renders a receipt that exists; it never creates one. The customer sees a
receipt (`hasInvoice`, `/order/{id}/invoice`, `/invoice/download`; a guest with
the order's `?uuid=` too) only once AADE registered it
(`Invoice.is_deliverable`: CONFIRMED and a stored PDF). `MYDATA_AUTO_SUBMIT`
makes the platform issue by itself (`MyDataConfig.issues_automatically()`: the
receipt at shipment, the credits on refunds); with it off the same work is done
by the order's admin actions ("Issue receipt (myDATA)", "Issue credit documents
(myDATA)"), which run whenever myDATA is ready. `MYDATA_INVOICE_SERIES_PREFIX`
and `MYDATA_CREDIT_SERIES_PREFIX` name the two series (`GRVP-2026`,
`GRVC-2026`); the credits have their own gapless counter (`CreditCounter`,
numbers `CRD-YYYY-NNNNNN`).

**The receipt is issued when the order ships.** ΕΛΠ art. 13(a): a retail
receipt for goods is issued "at the delivery or the start of the shipment". The
trigger is `order_shipped` (status SHIPPED, which the carrier polls and
webhooks reach through `update_order_status`, cash-on-delivery orders
included): `handle_order_shipped` dispatches `generate_order_invoice` when
`receipt_issuable(order)` holds (a RECEIPT order at SHIPPED, DELIVERED,
COMPLETED or RETURNED) and `issues_automatically()`. Exactly once:
`generate_invoice` returns the existing row, `submit_document` returns the
existing MARK, and a receipt that already has one is not dispatched again. The
payment rows are the ones `src/order/mydata/builder.py` always emitted
(card/online, cash for COD, the gift-card share under its own configurable
code). A refused or unanswered receipt is **never emailed**: the customer's
email (`send_invoice_email`, "Receipt ...") goes out only after AADE registered
it and the PDF carries the MARK; staff resubmit with "Send receipt to myDATA".
The 228 duplicate-uid MARK recovery through `RequestTransmittedDocs` is
unchanged.

**The receipt is for the consideration actually paid: net of money returned
BEFORE it.** A retail receipt (11.1) documents the sale at shipment; a credit
document (11.4) only corrects a receipt that exists (it relates to its MARK).
So money returned before the receipt is issued reduces the receipt and gets NO
credit document: `order.invoicing.refunded_before_receipt` adds up the
allocations (`CreditAllocation`) that moved before the receipt: the REFUND
rows on the payment ledger (less REFUND_REVERSAL, a refund the provider
reported failed), the value returned onto gift cards, and a chargeback staff
decided came with the goods back. Restored loyalty points are NOT in it:
points are a discount that already lowered the lines' gross, so the receipt
never held their value and a refund's points share is never allocated
("Points owe no credit" in [`fiscal-credit-vat-split.md`](fiscal-credit-vat-split.md)).
`_compute_vat_breakdown` takes
that gross off the rates exactly as a credit would
(`credit_documents.credit_breakdown`: the stored allocation's own rates, capped
per rate at what each holds, to the cent, the net derived cumulatively), so the
PDF, `Invoice.total`, `vat_breakdown` and the myDATA payload (`_receipt_lines`
uses the frozen breakdown, one line per rate, when the order's own lines no
longer add up to the receipt) agree. A refund AFTER the receipt row existed
gets its credit as before: `create_pending_credits` only reads REFUND rows
created at or after `invoice.created_at`, which is the other half of this rule.
If everything was returned before issuance there is no consideration left and
no receipt: `generate_invoice` refuses with `ReceiptNotIssuable` ("refunded in
full before its receipt was issued"; the shipment task logs it and issues
nothing; an existing receipt is never affected).

**Value returned onto gift cards is credited like a refund; restored points
are not.** After
the receipt exists, a settled `STORED_VALUE` `RefundAttempt` (value-based
refunds, section 7.2) reverses the sale just as a provider REFUND row does, so
`create_pending_credits` is the ONE path for both: it credits each such attempt
for what its rows actually returned onto gift cards
(`PaymentLedger.stored_gift_card_returned_by`, not the amount claimed; the
points it restored are a discount and owe no credit), as a `RetailCredit` of
reason REFUND linked by
`refund_attempt` (one document per attempt, exactly one of `refund` /
`refund_attempt`, enforced by the `credit_refund_iff_reason_refund` check), VAT
split by `credit_breakdown`, capped at what the receipt still holds. Its moment
is the first row naming the attempt (`stored_returned_at`), compared with the
receipt row's creation like a provider refund's: before it the receipt was
netted (no credit). `order_stored_value_returned` triggers the reconciliation.
A chargeback still has none. What was refunded before it nets the receipt at
the rates its allocation stored when it was claimed
(`invoicing._returned_before_receipt` adds up the `CreditAllocation` rows that
moved before the receipt, a refund by lines at its lines' own rates and a
goodwill amount at the rates it was spread over), and `invoicing._net_of` takes
each rate's gross off, capped per rate at what the rate holds: it never spills
what a rate cannot hold onto another.

**One cut-off: the receipt row's creation.** A NEW receipt nets everything
returned so far. A row that already exists (a retry after a failed render, or
`force=True`) keeps the figures it was issued with, MARK or not
(`_persist_invoice_row`), so money returned since is credited by
`create_pending_credits` and never also subtracted. The full-refund guard
(`_refuse_if_fully_refunded`) applies to every receipt without a MARK, on the
normal and the `force` path, and for an existing row counts only what was
returned before the row was created (`fully_refunded_before_receipt` passes the
row's creation to `refunded_before_receipt` as `before`); a receipt with a
MARK is frozen.

**A chargeback owes a credit document only when the goods came back, and staff
say so (decision M6, refined).** A lost dispute is the card network taking
money back, not the merchant reversing the sale, so the receipt stands when the
customer kept the goods. When they came back or never arrived the sale is
reversed in fact, and art. 12(4) (a credit "in the case of discounts or
refunds") applies. Nothing on the ledger tells the two apart, so an order with
a CHARGEBACK row offers the "Settle chargeback (goods)" action: «Το προϊόν
επιστράφηκε / δεν παραδόθηκε» or «Ο πελάτης κράτησε το προϊόν».
`order.chargebacks.decide_chargebacks` writes one `ChargebackDecision` per
CHARGEBACK row (no row = undecided), for every chargeback of the order, with
`decided_by`, `decided_at` and a note in the order history. Returned goes
through the one credit path: `create_pending_credits` credits each such
chargeback once (`RetailCredit.reason` CHARGEBACK, `chargeback` one-to-one with
the ledger row), for what the chargeback took, capped at what the receipt still
holds (`_capped_to_receipt`; a receipt with nothing left is refused with a
note, as for a refund), on the same cut-off as a refund with the decision as
its moment: a decision made before the receipt row existed nets the receipt
(`PaymentLedger.chargebacks_returned` inside `refunded_before_receipt`, so a
full return means no receipt), one made after it is credited. Kept credits
nothing. Idempotent. A returned decision can be changed back only until the
order has a receipt row (after it the receipt was netted or a credit is owed,
and `ChargebackDecisionError` refuses); kept can still become returned. An
undecided dispute is the dashboard counter "Lost disputes to settle"
(`OrderQuerySet.awaiting_chargeback_decision`, list filter
`?chargeback=undecided`), and the lost-dispute email says to decide. The
decision does not touch the ledger, `payment_status` or loyalty. A chargeback
recorded before the decision is not netted into a receipt
(`refunded_before_receipt` counts only decided rows).

**Credit documents.** `order.credit_documents.create_pending_credits(order_id)`
is the one reconciliation (under the order row lock, idempotent) and the
`issue_pending_credits` task wraps it and also resubmits any credit not
registered yet. It is dispatched by:

- `handle_order_refunded`: every refund path ends in
  `PaymentLedger.record_refund` and signals `order_refunded` once per REFUND
  row (in-app refund, the staff "Refund" action, `cancel_order`, Stripe
  `refund.created`, Viva reversal 1797), so each refund gets one document,
  `RetailCredit.refund` being one-to-one;
- `handle_order_post_save` when `payment_status` becomes CANCELED (an order
  closed unpaid, `Order.settle_unpaid_payment`: the refused or uncollected COD
  parcel that came back RETURNED);
- `decide_chargebacks` when staff decide the goods came back
  (`request_credit_documents`, the same `MYDATA_AUTO_SUBMIT` gate);
- the receipt's own success, for the credits that waited for its MARK.

What it creates: one credit per ledger REFUND row recorded after the receipt
row existed, for exactly the refunded amount; one per CHARGEBACK row whose
goods staff decided came back, decided after the receipt row existed (capped at
what the receipt holds); plus, once the order's payment is REFUNDED or
CANCELED, ONE settlement credit (`reason=SETTLEMENT`) for what the receipt
still holds and no credit covers (the whole receipt of a returned COD parcel;
the gift-card share of a fully refunded order). Nothing is created until the
receipt is CONFIRMED (it has a MARK and was not canceled): a credit cannot be
linked without it. A refund recorded before the receipt existed needs no
document (nothing was issued to credit), and neither does a cancellation before
shipment: `cancel_order` accepts only PENDING and PROCESSING, so it always
precedes the receipt. After shipment the order is RETURNED, and the refund (or
the COD settlement) is what credits it.

**VAT split.** Where a credit sits on the receipt's VAT rates is fixed when
the money is claimed (`CreditAllocation`). That part of this section is its
own file: [`fiscal-credit-vat-split.md`](fiscal-credit-vat-split.md).

**CancelInvoice** is lawful only for a document that was never used — never
delivered, sent or made available to the customer (Α.1138/2020 annex, «μη χρήση
παραστατικού»; AADE Q&A 2025 §8 Q24); once it has reached the customer the
correction is a credit document. Neither the law nor the API sets a time limit.
Receipts and credits are emailed as soon as AADE registers them, so in practice
a credit document is the tool. The admin actions ("Cancel receipt in myDATA
(issued in error)", and "Cancel in myDATA" on the Retail Credit Documents list)
do not enforce the "never used" rule yet; the guard is listed in issue #197.

**Invoices (B2B) are not issued by the platform.** From 2.11.2026 AADE decision
Α.1128/2025 art. 1(2) requires 1.1 and 5.1 documents to come from a licensed
provider or AADE's free timologio app; an ERP-issued invoice counts as not
issued (circular Ε.2004/2026). A `document_type=INVOICE` order therefore
produces no `Invoice`, no PDF, no email and no myDATA submission
(`receipt_issuable` refuses it; the 1.1 builder is gone). The checkout still
collects the buyer's company details; the order admin's "Billing / Invoice" tab
shows staff what timologio needs (`OrderAdmin.timologio_summary`: the identity
and the VAT rows of the order, from `src/order/taxation.py`) and records what
they issued (`Order.timologio_invoice_number` / `_mark`,
`timologio_credit_number` / `_mark`). The order confirmation says an invoice
was *requested* ("Invoice request"), never that one is attached.

**Settled questions** (research of 2026-10-14, primary sources in issue #197):
an e-shop needs a ΦΗΜ or a licensed provider for its retail receipts, so the
ERP channel needs a recorded exemption (above); the law sets no deadline for a
retail credit document (the "15th of the next month" rule is for credit
invoices, ΠΟΛ.1003/2014 §11.2.10, and applies to retail only by analogy);
`CancelInvoice` has no time limit, only the "never used" condition.
