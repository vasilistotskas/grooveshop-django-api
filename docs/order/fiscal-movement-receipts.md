# Order system: receipts with movement indication

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

This is part of §7.4, [`fiscal-mydata.md`](fiscal-mydata.md), between its
paragraphs on when the receipt is issued and on the consideration it is for.

**A courier or locker sale's receipt carries the movement and is issued when
its voucher or parcel is recorded, before the pickup (#747).** Α.1123/2024
Annex ΙΙ §7.3, as replaced by Α.1233/2026 (ΦΕΚ Β' 6576/30-10-2026), makes the
receipt «με ένδειξη διακίνησης αποθεμάτων» mandatory for goods delivered to
the consumer through a postal or courier entity unless the receipt is on the
parcel where it can be seen, or the carrier can show it at once; Α.1122/2024
art. 3(1) has it transmitted «πριν την έναρξη διακίνησης». Neither exemption
holds for an ACS or BOX NOW parcel. All of this applies only to a store whose
receipts the platform issues (`MyDataConfig.is_ready`, the one gate in
[`fiscal-mydata.md`](fiscal-mydata.md))
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
  the pickup needs. A blank store name, or a buyer without a name, a
  country or any part of their address, is a `BUILD` refusal, before
  anything is sent; a dispatch point missing any of its four parts is not
  set, so the store's receipts stay plain and are issued at SHIPPED
  (above). The receipt prints
  the buyer's name and address, with a "Movement of goods" box (purpose,
  start, loading and delivery place, unit). The block is decided when the receipt is first sent
  and stored on the row (`Invoice.movement`) with its identity fields: the
  PDF registered after it prints what AADE received, and a resubmission
  sends the document as issued, its dispatch date included, unless AADE
  refused it for its content (`movement.refused_for_its_content`: a
  validation code, `BUILD` or `XSD`; nothing was registered, so it is built
  anew, dated the day it is sent, never after the day its parcel left
  (`movement._starting_in_time`: one sent under the loss-of-connection
  procedure declares that day, any other is a `BUILD` refusal for staff,
  since AADE refuses a past start). Error 228 is no such refusal: AADE holds
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
  after P410 or settled). Recording it sends `order_shipment_dispatched`
  even when the order already holds those values (staff typed the voucher's
  own number first), as does any other change of the order's tracking (its
  first number, or a voucher recorded over a number staff typed), whose receiver
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
  as unanswered once `MYDATA_RETRY_AFTER_MINUTES` passed since that send
  began (`Invoice.mydata_sent_at`, never the row's last write)
  (`movement.unanswered_sends`); one whose credentials AADE refused keeps
  AADE's code and waits for staff. Before any SUBMITTED document is sent
  again its MARK is looked up (`service.look_up_mark`: `RequestTransmittedDocs`
  by its uid, the 228 recovery), since the send may have been registered.
  Credentials AADE refuses there are recorded as a message only: the last
  send keeps its "no answer", so a parcel that leaves is still stamped, and
  the sweep tries again every `MYDATA_RETRY_AFTER_MINUTES` until the
  credentials work (one order note, not one per try). Every send
  stores the movement block it carries with it, `{}` for a receipt sent
  plain, so a receipt rebuilt plain keeps no block AADE was not sent.
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
  elsewhere (or to a pickup point whose address is not known, so it cannot
  be compared), or leaves more than `MOVEMENT_START_MAX_DAYS` (5, Α.1123/2024
  art. 7(1) as replaced) after the receipt was issued, it logs an ERROR (the
  logged-errors alert) and leaves an order note saying what differs, for
  the store's accountant to decide how that movement is documented.
- **When myDATA refuses it** (a validation error). A data problem: the
  receipt is REJECTED with AADE's code and message on the row and in an
  order note, and counted on the dashboard ("Receipts rejected by myDATA");
  the parcel ships. Staff fix the data and press "Send receipt to myDATA". A
  pickup point whose address the carrier's catalogue does not hold is
  refused before sending with our own code `PICKUP`
  (`shipping.interfaces.PickupPointUnknown`): noted once, and built again
  by `retry_receipts_with_mydata` every `MYDATA_RETRY_AFTER_MINUTES` until
  a catalogue sync adds it. The address is read from the catalogue at each
  build: an ACS station by the exact (station, branch) pair the voucher
  books, never the code-only fallback row checkout may have linked, and a
  booking without a branch only by the row without one; a BOX NOW locker
  by its id.
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
  "never used" rule, "CancelInvoice" in
  [`fiscal-mydata.md`](fiscal-mydata.md); AADE's annex §13 lets only a movement document
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
    against the myDATA sandbox on 2026-11-20): within
    `MYDATA_CANCEL_NOT_FOUND_RETRY_MINUTES` (10) of the registration
    (`mydata_confirmed_at`) that is retried like no answer. After them AADE
    does not hold the MARK for the credentials asking (a VAT or credentials
    change, sandbox against production): a refusal like any other,
    recorded on the row (the admin's cancel of a document issued in error
    too). A refusal, or a courier that
    never answered: the parcel may be on its way, so the receipt stands,
    reaches the customer, and goes the credit path as before; one never
    sent (its worker stopped before the send), or whose last send has no
    answer, is sent then, its MARK looked up first, since the hold was what
    the sweep sent it for.
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
    nothing, `MyDataNotListedError`, and keeps it set aside), so a cancel
    whose answer was lost is recorded as such and never ends as a
    cancellation and a credit. A receipt still unregistered (its sends went
    unanswered) is sent first, and its registration queues the cancel.
  - **A receipt myDATA cannot see is held for staff.** A 301 refusal of the
    cancel (past the window above) and a listing without the receipt mean
    the store's current credentials do not reach it: it may be registered,
    or canceled already, under others (a VAT or credentials change, sandbox
    against production). A credit could count a tax document twice and
    asking again changes nothing, so `tasks._hold_for_staff` stamps
    `Invoice.cancel_held_for_staff_at` and writes one order note naming the
    cause; the receipt stays set aside, the sweep leaves it, and every
    later settle returns at once. A listing that does not answer (a
    timeout, a 5xx) is no such answer and is retried as before. Staff check
    with the accountant, then resolve it from the order: "Cancel receipt in
    myDATA (issued in error)" cancels it once AADE sees it, and "Issue
    credit documents (myDATA)" releases it to the credit path
    (`tasks.credit_receipt_held_for_staff`: it stands, reaches the
    customer, and its credit is issued).
  - **The dashboard** counts receipts still set aside after
    `MYDATA_RETRY_AFTER_MINUTES` ("Receipts of canceled orders not canceled
    in myDATA"), the ones held for staff included; the order's notes say
    why, and for a held one what staff do. Nothing about the cancel or the
    refund waits for it.
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
