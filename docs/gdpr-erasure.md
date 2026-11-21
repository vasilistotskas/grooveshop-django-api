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
  `orders_in_motion`.

The export lists all three: an order's fields and metadata parts, plus what
each carrier reported about its recipient (`carrier`). Erasure clears the same
three. `tests/unit/user/test_erasure_covers_every_order_personal_field.py`
holds `PERSONAL_FIELDS` to the staging anonymiser's list.

## What erasure does to an order

`order.privacy.erase_orders`, for every order of the subject, soft-deleted
ones included (`all_with_deleted`: an admin delete only hides an order):

1. **Carriers** (`erase_recipient_data`). This runs first, while the order
   still holds the buyer's data.
   - ACS clears its shipment metadata at `AcsShipment.PERSONAL_METADATA`: the
     failed request, the notes the voucher carried, and the cancel reason.
   - It deletes the call history (`AcsApiCall`) of the orders and their
     vouchers. A call is found by its order or voucher column, or by one of
     the order's voucher numbers, as a whole number, in its request or answer.
     A name or a phone is never used, because two customers can share one.
   - It clears checkpoint notes and a COD payout's `parcel_receiver`, in their
     columns and in the raw rows. The payout's `pod` stays: it is the voucher
     number ACS reported.
   - BOX NOW clears its metadata at `BoxNowShipment.PERSONAL_METADATA`,
     `cancellations[*].reason` included, and each parcel event's text.
2. **Copies in the order's own records.** The buyer's email, name, phone and
   cancel reason, read before the order is cleared, become `[deleted]` in its
   history (`OrderHistory`: "email sent to …", a refund's reason), the stock
   log, the refund attempts and the ledger rows. A copy cut at its column's
   length is matched by its start.
3. **The order.** `PERSONAL_FIELDS` are blanked, or set to placeholders for
   the names, email, street and city. `BILLING_FIELDS` are kept only when an
   invoice was issued in timologio (`timologio_invoice_mark` or
   `timologio_invoice_number`). The retail receipt keeps its own
   `buyer_snapshot`, a legal document. `PERSONAL_METADATA` is dropped, and the
   order is stamped `personal_data_erased_at`.

## Carrier data that arrives after the erasure

A parcel still moving goes on being tracked, delivered and paid out, and each
poll, webhook and COD answer writes the recipient's details again.
`user.tasks.erase_late_carrier_data` runs daily, fanned out to every store
(suspended included), and runs the carrier erase again for an erased order in
two cases:

- while one of its parcels is in motion (`orders_in_motion`): not in a
  terminal state, or an ACS parcel delivered with cash to collect and no
  payout yet;
- for `ERASURE_CARRIER_FOLLOW_UP_DAYS` (90) after the erasure in any case. That
  covers what a carrier can still report about a settled parcel: a BOX NOW
  event is admitted for 90 days (`BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS`), and a
  COD payout follows delivery by a few days.

What arrives is cleared within a day.

## Tests

`tests/integration/user/test_gdpr_erasure_completeness.py::test_no_value_of_the_subject_survives_on_what_erasure_keeps`
puts the subject's data where the code puts it: two orders are canceled
through `OrderService.cancel_order` with a typed reason (a restock, a refund,
both parcels canceled), the carriers' records are filled, and a third order is
soft-deleted. After the erasure, the test searches every text and JSON column
of every table in the schema for each value.
