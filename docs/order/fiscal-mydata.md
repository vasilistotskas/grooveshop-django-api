# Order system: retail tax documents (myDATA)

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 7.4 Retail tax documents (myDATA): a receipt at shipment, a credit per refund

`src/order/accounting/mydata/` (builder, client, parser, service),
`src/order/accounting/invoicing.py`, `src/order/accounting/movement.py`,
`src/order/accounting/credit_documents.py`,
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
`order.accounting.invoicing.specimen_available` is the one gate, used by the endpoint and
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
(`Invoice.is_deliverable`: CONFIRMED and a stored PDF, and for a receipt
with movement indication its parcel left). `MYDATA_AUTO_SUBMIT`
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
existing MARK, and a receipt that already has one is not dispatched again.
That is the path of an order no carrier takes (the merchant's own
`flat_rate` delivery); a courier or locker order's receipt was issued
before the pickup (below), so SHIPPED issues nothing for it. The
payment rows are the ones `src/order/accounting/mydata/builder.py` always emitted
(card/online, cash for COD, the gift-card share under its own configurable
code). A refused or unanswered receipt is **never emailed**: the customer's
email (`send_invoice_email`, "Receipt ...") goes out only after AADE registered
it and the PDF carries the MARK; staff resubmit with "Send receipt to myDATA".
The 228 duplicate-uid MARK recovery through `RequestTransmittedDocs` is
unchanged. The PDF rendered before the registration prints no MARK, so the
save that records the MARK clears it (`service._drop_the_unregistered_pdf`)
and `send_invoice_to_mydata` renders the registered one: until then the
receipt is not deliverable (no stored PDF), and a render that fails leaves
a row without a PDF, which that task renders first when it runs again,
without a second submission. A re-render replaces the stored PDF only
once the new one exists (`invoicing._store_pdf`), so a failed one leaves
the old file in place, and only while the row's MARK is the one it
prints: a render that a registration overtook is discarded.

**A courier or locker sale's receipt carries the movement and is issued when
its voucher or parcel is recorded, before the pickup (#747).** Α.1123/2024
Annex ΙΙ §7.3, as replaced by Α.1233/2026 (ΦΕΚ Β' 6576/30-10-2026), makes the
receipt «με ένδειξη διακίνησης αποθεμάτων» mandatory for goods delivered to
the consumer through a postal or courier entity unless the receipt is on the
parcel where it can be seen, or the carrier can show it at once; Α.1122/2024
art. 3(1) has it transmitted «πριν την έναρξη διακίνησης». Neither exemption
holds for an ACS or BOX NOW parcel. All of this applies only to a store whose
receipts the platform issues (`MyDataConfig.is_ready`, the one gate above)
and that has set its dispatch point (`movement.movement_receipts_on`: the
`MYDATA_LOADING_STREET` / `_NUMBER` / `_POSTAL_CODE` / `_CITY` settings,
all four required: the dispatch point is also the issuer's address, whose
street number AADE requires). A store that issues in its own ΦΗΜ or provider
sees no change at all; one without a dispatch point keeps the plain receipt
at SHIPPED, and its dashboard (when ACS or BOX NOW is active) asks for the
dispatch point. For an order whose carrier keeps a shipment of its own
(`ShippingService.moves_by_carrier`):

- **The document** (`src/order/accounting/movement.py`, the builder's
  `_emit_movement`): the same 11.1 with `dispatchDate` (the day the receipt
  is issued, which is the day the voucher or parcel is created: a later day,
  such as the next business day after `DISPATCH_CUTOFF`, would date the
  document after the movement started, and AADE refuses an earlier one,
  error 280; no `dispatchTime`, optional in phase A and not known when the
  receipt is issued), `movePurpose` 1 «Πώληση»,
  `otherDeliveryNoteHeader` (`loadingAddress`: the dispatch point;
  `deliveryAddress`: the ACS station's or the BOX NOW locker's address for a
  pickup order, `ShippingCarrierInterface.pickup_point_address`, BOX NOW's
  city being its `addressLine2`, otherwise the order's shipping address) and
  `isDeliveryNote` true; every good that moves is declared with its real
  `quantity` (the units bought less those refunded,
  `movement.moving_goods`, stored with the block) and `measurementUnit` 1
  «Τεμάχια»: on its own money line, otherwise on a line of its own with
  `netValue` and `vatAmount` 0 at the good's VAT category, which changes
  no total. That is a free gift (it has no money line) and every good of a
  receipt netted by an earlier refund (one money line per rate, mixing
  goods and shipping); shipping and the fee carry no quantity. myDATA REST
  API v2.0.2 §5.4 gives `netValue` and `vatAmount` a minimum of 0, and its
  error table refuses no zero-value line in general (a receipt with one
  verified against the myDATA sandbox on 2026-11-20). `movePurpose` stays
  1 «Πώληση» for a gift: it moves with the sale.
  **What AADE requires of a movement document beyond the XSD** (verified
  against the myDATA sandbox on 2026-11-20: it refused every 11.1 with
  `isDeliveryNote` without these, although its documentation forbids a
  Greek party's name and address on other documents, errors 219 and 220):
  an `itemDescr` on every
  line (error 230; the product's name, the charge's label, or for a
  per-rate line "Goods and charges at N% VAT"), the issuer's `name` and
  `address` with its street number, and a `counterpart` with `name` and
  `address`, its number included (error 204). The issuer is named by the
  store's `INVOICE_SELLER_NAME` at its dispatch point (the store's own
  address is one free line, which holds no number apart); the counterpart
  is the buyer, by name and own address (`movement.MovementParty`, stored
  with the block), with `vatNumber` `000000000`, the value AADE names for
  a party without one (error 289; a Greek and a Cypriot buyer were both
  accepted), and the header carries `nonObligatedRecipient` (a recipient
  who takes no part in the digital movement process, §5.3 note 23).
  Never `withoutDigitalTransportTracking`: such a document is "Completed"
  at issue and AADE refuses to cancel it (error 801), which a cancel before
  the pickup needs. A missing store name, dispatch point number or buyer
  address is a `BUILD` refusal, before anything is sent. The receipt prints
  the buyer's name and address, with a "Movement of goods" box (purpose,
  start, loading and delivery place, unit). The block is decided when the receipt is first sent
  and stored on the row (`Invoice.movement`) with its identity fields: the
  PDF registered after it prints what AADE received, and a resubmission
  sends the document as issued, its dispatch date included, unless AADE
  refused it for its content (`movement.refused_for_its_content`: a
  validation code, `BUILD` or `XSD`; nothing was registered, so it is built
  anew, dated the day it is sent). Error 228 is no such refusal: AADE holds
  the document as it was sent. AADE does not apply error 280 to a
  `transmissionFailure` document, so one whose parcel left during an outage
  keeps its date. Every payload is checked against AADE's
  v2.0.2 XSDs, vendored in `src/order/accounting/mydata/xsd/`, before it is
  sent.
- **AADE's rules this section relies on, and which were checked.** #760
  was sent to AADE's myDATA sandbox (`mydataapidev.aade.gr`) through the
  platform's own builder, service and client.
  *Verified against the myDATA sandbox on 2026-11-20:* the movement
  document's shape above (`itemDescr` on every line, error 230; issuer
  name and address with its number, counterpart with name and address
  and its number, error 204; counterpart `vatNumber` `000000000` for a GR
  and a CY buyer; `nonObligatedRecipient`), registered for a home
  delivery, an ACS station, a BOX NOW locker, cash on delivery, a
  zero-value gift line and `transmissionFailure` 3; a plain 11.1; the uid
  (SHA-1 over the fields joined by `-`, upper-case hex); a
  `withoutDigitalTransportTracking` document refused cancel (error 801);
  `CancelInvoice` of an 11.1 with movement, and 301 for one sent within a
  second of the registration (seen in one run, not in the next, where the
  same cancel went through at once); `RequestTransmittedDocs` listing the
  uid, `qrCodeUrl`, the movement's `dispatchDate` and each cancellation;
  error 280 for a document issued and dated the day before, and the same
  document registered with `transmissionFailure` 3; error 228 for the
  same document sent again; a registered receipt whose answer was lost
  recovered by `look_up_mark` with no second send; and a receipt issued
  the day before, its send unanswered and nothing registered, found
  `ABSENT`, sent again dated today and registered.
  *Not exercised, unverified:* the service's handling of a 228 answer (the
  answer is verified, its handling only by tests, since the lookup runs
  first); a `transmissionFailure` document dated more than a day back;
  error 289's own condition; whether AADE wants the issuer's seat rather
  than its dispatch point as the issuer's address (it accepts either
  shape; the platform sends the dispatch point, since the seat is one free
  line); the `MOVEMENT_START_MAX_DAYS` limit, which is the decision's, not
  the API's.
- **The buyer's personal data.** AADE requires the counterpart's name and
  address on a movement document (error 204, verified against the myDATA
  sandbox on 2026-11-20); that requirement is the ground for sending them,
  and they are sent nowhere else. They are stored with the block
  (`Invoice.movement`), which the staging refresh scrubs
  (`src/devtools/anonymise/rules.py`, `order.Invoice`), like the receipt's
  `buyer_snapshot`.
- **When it is issued.** The order is issuable before it ships
  (`receipt_issuable`: PENDING or PROCESSING, nothing owed online, and the
  carrier holds the parcel, `ShippingService.carrier_holds_the_parcel`:
  ACS's voucher, BOX NOW's parcel; neither the shipment row checkout
  writes nor a tracking number staff type on the order is one). Every
  path that makes a shipment live records it through `Order.add_tracking_info`
  (an ACS voucher minted or adopted, a BOX NOW parcel created, taken over
  after P410 or settled), and the order's first tracking number sends
  `order_shipment_dispatched`, whose receiver
  (`issue_receipt_when_the_shipment_is_live`) calls
  `order.tasks.issue_receipt_for_pickup`: `generate_order_invoice` is queued
  while `issues_automatically()`. A store that issues by hand issues it from
  the order ("Issue receipt (myDATA)") before handing the parcel over.
  **Shipping never waits for myDATA**: the voucher, the pickup list and the
  handover go ahead whatever AADE answers.
- **When myDATA does not answer** (a transport error, AADE's
  `TechnicalError`, or an answer that is not XML, such as an empty body
  behind an HTTP 200, which the parser turns into a transport error). The
  receipt keeps its number and is recorded as issued; the send is marked
  unanswered (`mydata_error_code` `NO_ANSWER`, status SUBMITTED). A send
  whose worker stopped mid-request is left SUBMITTED with no code, and counts
  as unanswered once `MYDATA_RETRY_AFTER_MINUTES` passed
  (`movement.unanswered_sends`); one whose credentials AADE refused keeps
  AADE's code and waits for staff. Before any SUBMITTED document is sent
  again its MARK is looked up (`service.look_up_mark`: `RequestTransmittedDocs`
  by its uid, the 228 recovery), since the send may have been registered.
  While the parcel is still with the store, a resend is the document as
  issued, plainly: one timeout is no loss of connection. Its `dispatchDate`
  is the one thing that moves: AADE refuses a date before the current one
  (error 280), so a plain resend on a later day is dated today, and only
  when the lookup read AADE's whole window and found no document under the
  uid (`MarkLookup.ABSENT`; a listed document without a uid, which the
  schema allows, makes it `UNKNOWN`. The uid is AADE's: SHA-1 over the
  identity fields joined by `-`, in upper-case hex, `uid.build_uid`,
  verified against the myDATA sandbox on 2026-11-20 (the documentation
  names the fields, not the join); any other form never matches what AADE
  lists); a registered one keeps the date AADE holds, and a MARK the lookup recovers brings it back: the row's block
  takes the `dispatchDate` AADE lists, since the send that registered it
  may not be the one whose block the row holds. **Once the parcel leaves (SHIPPED) without a MARK**, its
  movement started during a loss of connection, AADE's procedure:
  Α.1233/2026 annex §12.1 has such a movement document issued «με διακριτή
  ένδειξη για την απώλεια διασύνδεσης» and transmitted «άμεσα με την
  επαναφορά της» (a distinct series is asked only of one issued by hand).
  `movement.parcel_left_unregistered` (in `handle_order_shipped`) stamps
  `Invoice.transmission_failed_at` on an unanswered receipt and sends it at
  once; a send that goes unanswered after the parcel left is stamped where
  the answer is missed (`service._persist_send_error`), so whichever comes
  second stamps it. A sender that built the document before the stamp
  landed (it was looking the MARK up) finds it when it stores its intent
  under the row lock, and builds the document again. From then on every transmission carries
  `transmissionFailure` 3, «Απώλεια διασύνδεσης», the one value of that
  element (`xs:byte` 1-4 in the v2.0.2 XSD) the REST API documentation
  allows an ERP to send (1, 2 and 4 are a licensed provider's). A receipt
  AADE refused, or whose credentials it refused, is not stamped: that is no
  loss of connection. The task retries as before, and
  `order.tasks.retry_receipts_with_mydata` (beat
  `retry-receipts-with-mydata`, every 5 minutes, every store suspended or
  not) sends every unanswered receipt with movement not touched for
  `MYDATA_RETRY_AFTER_MINUTES` (15) again, up to `MYDATA_RETRY_SWEEP_BATCH`
  per run, least recently touched first, until it is registered. Overlapping sends are harmless: they
  carry the same uid, AADE registers one and answers the other with 228,
  and `submit_document` never demotes a row that holds a MARK (every write
  re-reads it under its lock). Staff see one warning on the dashboard
  ("Parcels that left while myDATA was unreachable", with the count) and
  need do nothing.
- **It reaches the customer with the parcel.** A receipt with movement is
  not emailed nor offered for download before its parcel leaves
  (`Invoice.awaits_the_parcel`, in `is_deliverable`). Each side writes, then
  reads the other fresh: `handle_order_shipped` runs after SHIPPED commits
  and queues the email for a CONFIRMED receipt with a MARK;
  `send_invoice_to_mydata` reads the order again after it stores the MARK
  and queues it when the parcel left. Whichever comes second sends it, the
  email task's own flag keeps it to one, and the task checks
  `is_deliverable` on the rows when it runs, so a receipt set aside or
  canceled since is never sent. Until then the document is issued but not
  used, which is what lets a cancel before the pickup cancel it (below).
- **A voucher made again after the receipt was issued** (a new locker,
  station or address, or days later). The receipt is AADE's record of the
  sale and is not touched; when the parcel leaves,
  `movement.flag_a_movement_the_receipt_does_not_cover` compares the
  movement that took place with the one declared, and when the parcel goes
  elsewhere, or leaves more than `MOVEMENT_START_MAX_DAYS` (5, Α.1123/2024
  art. 7(1) as replaced) after the receipt was issued, it logs an ERROR (the
  logged-errors alert) and leaves an order note saying what differs, for
  the store's accountant to decide how that movement is documented.
- **When myDATA refuses it** (a validation error). A data problem: the
  receipt is REJECTED with AADE's code and message on the row and in an
  order note, and counted on the dashboard ("Receipts rejected by myDATA");
  the parcel ships. Staff fix the data and press "Send receipt to myDATA". A
  pickup point whose address is not known is refused before sending
  (BUILD).
- **The QR on the parcel.** The registered receipt PDF carries AADE's QR
  (`mydata_qr_url`, the `qrUrl` of the response; after a MARK recovery,
  the `qrCodeUrl` `RequestTransmittedDocs` lists, which it did for every
  document, verified against the myDATA sandbox on 2026-11-20). Printing it
  with the parcel is what §7.3
  allows («δύναται επί του δέματος να επικολλάται ... QR code»); neither
  carrier's label takes our content (ACS returns its own label PDF, BOX NOW
  its own).
- **Canceled before the pickup: the receipt is canceled, not credited.**
  The sale and the movement never took place and the document was never
  handed to the customer, the one case `CancelInvoice` is lawful for (the
  "never used" rule below; AADE's annex §13 lets only a movement document
  *without* value be canceled merely because the movement had not started,
  so the 11.1 rests on that rule).
  - **Set aside.** The one path that cancels an order
    (`OrderService._cancel_order_locked`) sets the receipt aside under its
    lock on the order row, before the save that asks for the credits
    (`invoicing.hold_receipt_for_cancel`: `Invoice.cancel_requested_at`, for
    a carrier order that has not shipped, only while the platform issues
    the store's receipts). Credits are created under the same lock and wait
    while it is set (`create_pending_credits`), and the email task reads it
    before it sends, so neither can act between the cancel and the hold.
    Set aside is a receipt not handed to the customer: one with movement,
    or one AADE has not registered (never sent, or refused; a refused one
    is canceled once staff correct it and it is registered, and the order
    note says so). A receipt that already has a credit document (money went
    back before the pickup), or a plain one AADE registered (emailed at its
    MARK), was used: it is never set aside, and the rest of it goes the
    credit path. `order_canceled`, after the commit, queues the settle.
  - **Decided by the courier's answer** the cancel paths record
    (`metadata['cancellation']['shipment_cancel']`, [§7.1](cancel.md)):
    `order.tasks.settle_canceled_receipt` runs when the cancel commits (a
    customer cancel already holds the answer), whenever an answer is
    recorded later (`_record_shipment_cancel` sends
    `order_shipment_cancel_answered`: the staff cancel's own ask and
    `finish_pending_shipment_cancels`), and from
    `retry_receipts_with_mydata`. One settle per receipt at a time: it holds
    a claim (`cache.lock`, `MYDATA_SETTLE_CLAIM_SECONDS`, 300) from the read
    of the state, under the order's and the receipt's row locks, across the
    AADE calls, so a second run returns at once instead of sending
    `CancelInvoice` again; a claim a dead worker left expires, and the next
    run finds the cancellation AADE recorded. Stopped, or no parcel to stop: the receipt is
    canceled in myDATA (`cancel_document`, an order note with the
    cancellation MARK) and no credit follows. AADE answers a cancel sent
    within a second of the registration with 301 ("not found"; verified
    against the myDATA sandbox on 2026-11-20): that is retried like no
    answer, never read as a refusal. A refusal, or a courier that
    never answered: the parcel may be on its way, so the receipt stands,
    reaches the customer, and goes the credit path as before; one never
    sent (its worker stopped before the send) is sent then, since the hold
    was what the sweep sent it for.
  - **myDATA's answers.** Only AADE refusing the cancel
    (`MyDataValidationError`) sends the receipt the credit path too, with the
    reason in an order note. Credentials AADE refuses keep it set aside with
    a note for staff; no answer is retried (`autoretry_for` the transient
    network set, which `MyDataTransportError`, a `ConnectionError`, is in),
    then by the sweep. Before any receipt goes the credit path, AADE is
    asked whether it already holds a cancellation of it
    (`service.find_cancellation_mark`: `RequestTransmittedDocs` from its
    MARK within a day either side of its issue date, every page of that
    window read, never a page count: the document's `cancelledByMark` or
    `cancelledInvoicesDoc`; a document AADE does not list concludes
    nothing and keeps it set aside), so a cancel whose answer was lost is
    recorded as such and never ends as a cancellation and a credit. A
    receipt still unregistered (its sends went unanswered) is sent first,
    and its registration queues the cancel. The dashboard counts receipts
    still set aside after `MYDATA_RETRY_AFTER_MINUTES` ("Receipts of
    canceled orders not canceled in myDATA"); the order's notes say why.
    Nothing about the cancel or the refund waits for it.
- **Turnover.** The turnover report (`src/order/reporting/turnover.py`,
  [turnover-report.md](../turnover-report.md)) still counts a sale when the
  parcel leaves (`Order.shipped_at`), not on the receipt's date: until the
  pickup the sale can be undone and its receipt canceled as never used. A
  voucher created in the last days of a month whose parcel leaves in the
  next puts the receipt in myDATA's earlier month and the sale in the
  report's later one.
- **What stays.** An order no carrier takes keeps the plain receipt at
  SHIPPED, and SHIPPED issues nothing twice (`generate_invoice` returns the
  existing row). An order canceled after its parcel left, or whose goods
  come back, goes through the refund and credit path unchanged.

**The receipt is for the consideration actually paid: net of money returned
BEFORE it.** A retail receipt (11.1) documents the sale at shipment; a credit
document (11.4) only corrects a receipt that exists (it relates to its MARK).
So money returned before the receipt is issued reduces the receipt and gets NO
credit document: `order.accounting.invoicing.refunded_before_receipt` adds up the
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
`order.accounting.chargebacks.decide_chargebacks` writes one `ChargebackDecision` per
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

**Credit documents.** `order.accounting.credit_documents.create_pending_credits(order_id)`
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
document (nothing was issued to credit). A cancellation before shipment
precedes the receipt only for an order no carrier takes: a courier or locker
order's receipt is issued when its voucher or parcel is created, and a cancel
before the pickup cancels it in myDATA instead (above). After shipment the order is RETURNED, and the
refund (or the COD settlement) is what credits it.

**VAT split.** Where a credit sits on the receipt's VAT rates is fixed when
the money is claimed (`CreditAllocation`). That part of this section is its
own file: [`fiscal-credit-vat-split.md`](fiscal-credit-vat-split.md).

**CancelInvoice** is lawful only for a document that was never used — never
delivered, sent or made available to the customer (Α.1138/2020 annex, «μη χρήση
παραστατικού»; AADE Q&A 2025 §8 Q24); once it has reached the customer the
correction is a credit document. Neither the law nor the API sets a time limit.
Receipts and credits are emailed as soon as AADE registers them, so in practice
a credit document is the tool, except for a receipt with movement indication,
which reaches the customer only when its parcel leaves: an order canceled
before that has it canceled (above). The admin actions ("Cancel receipt in myDATA
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
and the VAT rows of the order, from `src/order/accounting/taxation.py`) and records what
they issued (`Order.timologio_invoice_number` / `_mark`,
`timologio_credit_number` / `_mark`). The order confirmation says an invoice
was *requested* ("Invoice request"), never that one is attached.

**Settled questions** (research of 2026-10-14, primary sources in issue #197):
an e-shop needs a ΦΗΜ or a licensed provider for its retail receipts, so the
ERP channel needs a recorded exemption (above); the law sets no deadline for a
retail credit document (the "15th of the next month" rule is for credit
invoices, ΠΟΛ.1003/2014 §11.2.10, and applies to retail only by analogy);
`CancelInvoice` has no time limit, only the "never used" condition.
