# GDPR erasure and data export

A customer erases their account and exports their data from the storefront's
Privacy page. `src/user/services/gdpr.py` does both: `compile_user_data` for
the export and `anonymise_and_delete_user` for the erasure. Orders are kept
(tax law, payment reconciliation, finance reports), so erasure clears the
buyer's data from them instead of deleting them. `src/order/privacy.py` holds
what that means. The code wins over this page.

## One definition of an order's personal data

- `Order.PERSONAL_FIELDS`: contact, delivery address, the customer's note, and
  the invoice's counterparty (`Order.BILLING_FIELDS`).
- `Order.PERSONAL_METADATA`: the paths in `metadata` that are the buyer's: the
  browser identity the Conversions API reads (`meta`: IP address, user agent,
  `fbp`, `fbc`) and the typed cancel reason (`cancellation.reason`).
- Each carrier's own records, through the carrier interface
  (`src/shipping/interfaces.py`): `erase_recipient_data`, `recipient_data` and
  `orders_written_since`.

The export lists all three: an order's fields and metadata parts, plus what
each carrier reported about its recipient (`carrier`). Erasure clears the same
three. `tests/unit/user/test_erasure_covers_every_order_personal_field.py`
holds `PERSONAL_FIELDS` to the staging anonymiser's list.

## Whose orders

`_subject_orders` in `src/user/services/gdpr.py` is the one queryset the
export and the erasure read. It covers the account's orders and the guest
orders placed with one of its verified addresses, as every other guest record
is. Soft-deleted ones are included (`all_with_deleted`), because an admin
delete only hides an order. What hangs off those orders goes with them: a
guest order's promotion redemption and Conversions API logs carry no user,
so they are found by the order.

## What erasure does to an order

`order.privacy.erase_orders`:

1. **The buyer's values are read first.** The order's email, name, phone (also
   its national ten digits) and typed cancel reason are taken while the order
   still holds them. The copies in free text are found by them.
2. **Carriers** (`erase_recipient_data`, given those values for the text
   staff wrote).
   - **ACS:**
     - It clears its shipment metadata at `AcsShipment.PERSONAL_METADATA`:
       the failed request, the customer's note the voucher carried, and the
       cancel reason.
     - It deletes the call history (`AcsApiCall`) of the orders and of every
       voucher they ever had: live, multipart children, `previous_vouchers`
       of a re-mint, `orphan_vouchers`, `adopted_voucher`. A call is found by
       its order or voucher column, or by one of those voucher numbers, as a
       whole number, in its request or answer. A name or a phone is never
       used, because two customers can share one.
     - It clears checkpoint notes and a COD payout's `parcel_receiver`, in
       their columns and in the raw rows. The payout's `pod` stays: it is the
       voucher number ACS reported.
     - It redacts the buyer's values from staff's notes: a COD received
       outside ACS (with its history copies) and a payout's reconciliation
       note.
   - **BOX NOW:**
     - It clears its metadata at `BoxNowShipment.PERSONAL_METADATA`,
       `cancellations[*].reason` included.
     - It clears each parcel event's text and its raw body at
       `BoxNowParcelEvent.PERSONAL_RAW`, which holds the webhook's `customer`
       (name, email, phone) and the event text.
3. **Copies in the order's own records.** The buyer's values become
   `[deleted]` in the order's history (`OrderHistory`: "email sent to …", a
   refund's reason), the stock log, the refund attempts and the ledger rows
   (`core.utils.redaction`). A copy cut at its column's length is matched by
   its start.
4. **The order.**
   - `PERSONAL_FIELDS` are blanked, or set to placeholders for the names,
     email, street and city.
   - `BILLING_FIELDS` are kept when an invoice is issued or owed
     (`order.privacy.keeps_billing`): a timologio MARK or number is
     recorded, or the order is an INVOICE order that was not canceled, which
     staff issue by hand from those fields. The retail receipt keeps its own
     `buyer_snapshot`, a legal document.
   - `PERSONAL_METADATA` is dropped, and the order is stamped
     `personal_data_erased_at`.

## Carrier data that arrives after the erasure

A parcel still moving goes on being tracked, delivered and paid out, and each
poll, webhook and COD answer writes the recipient's details again. That
includes the last one, which often names who took delivery.

`user.tasks.erase_late_carrier_data` runs daily, fanned out to every store
(suspended included). It runs the carrier erase again for an erased order
when a carrier wrote a record of it within `ERASURE_CARRIER_SWEEP_LOOKBACK_DAYS`
(7). A record is any of these:

- a tracking checkpoint;
- a COD payout, matched by its shipment or by its voucher alone;
- an ACS call naming the order or any voucher it had (a call with neither
  column, a COD or pickup-list answer, is searched for the voucher);
- a BOX NOW event or shipment row;
- an ACS shipment row whose personal metadata a writer changed (those
  writers bump `updated_at`).

Each carrier reads those rows from the ones written since then
(`orders_written_since`), so the sweep's cost follows what the carriers
wrote, not how many orders were ever erased. The lookback is longer than any
gap between two daily runs, so no write is missed. What arrives is cleared
within a day.

The same sweep clears the billing fields of an erased order that does not
need them: an INVOICE order that was owed an invoice at erasure and was
canceled since.

## Half-erased orders

The same daily sweep, as its last step, finishes the orders an erasure left
half done (`order.privacy.finish_earlier_erasures`): those with the
placeholder email `deleted-<hex>@deleted.invalid` and no
`personal_data_erased_at` stamp. It finds them through the email's trigram
index (`order_email_search_ix`).

- It works in batches of 50, each one transaction: their carrier records are
  cleared, the cancel reason's copies and every email address in their
  history are redacted (the buyer's own address is gone), their
  `PERSONAL_METADATA` is dropped and the billing rule applied, and the stamp
  comes last.
- A batch that fails is rolled back, logged and left for the next day. The
  next batch still runs (the batches page by id), and the rest of the sweep
  has already run.

The step is idempotent: a stamped order is never matched again. The sweep
can also be run by hand from the platform admin's schedule page
(`erase-late-carrier-data`).

A soft-deleted order that erasure skipped was unlinked from its account with
its data. It cannot be told apart from a guest's, so it cannot be found.

## Tests

`tests/integration/user/test_gdpr_erasure_completeness.py::test_no_value_of_the_subject_survives_on_what_erasure_keeps`
puts the subject's data where the code puts it: two orders are canceled
through `OrderLifecycleService.cancel_order` with a typed reason (a restock, a refund,
both parcels canceled), the carriers' records are filled, and a third order is
soft-deleted. After the erasure, the test searches every text and JSON column
of every table in the schema for each value.
