# Monthly turnover report

A store's turnover for each calendar month: sales without VAT and without
shipping, less what went back. Source: `order/turnover.py` (the definition and
every query), `admin/turnover.py` (the page and the CSV),
`core/templates/admin/turnover.html`, `admin/admin.py: MyAdminSite.get_urls`
(the two routes), `admin/navigation.py: store_sidebar` (the entry),
`order/models/order.py` (`Meta.permissions`). The code wins over this page.

## The definition

`net turnover = net sales - returns`, per month, in the store's currency.

### Which orders are sales

An order whose payment was collected: `payment_status` is `COMPLETED`,
`PARTIALLY_REFUNDED` or `REFUNDED` (`SALE_PAYMENT_STATUSES`). `Order.status` is
not read.

- Cash on delivery counts when the courier's money is booked
  (`OrderService.settle_cod_payment` sets `COMPLETED`), not when the parcel
  ships. A parcel that came back unpaid closes with `payment_status` `CANCELED`
  (`Order.settle_unpaid_payment`) and is never a sale.
- A canceled, failed or unpaid order is never a sale. A canceled order that was
  paid is refunded: it is a sale in its month and a return in the month of the
  refund.
- Why payment and not shipment: the ledger is the only record that always has a
  date for a refund, and a sale that is paid but later refunded must net out
  against the same figures. The receipt is issued at shipment (docs/order-system.md
  section 7.4), but most stores issue receipts outside the platform, so no
  receipt table can be the base.

### The month of a sale

The month the order was placed (`Order.created_at`), in the store's timezone.
The timezone is `settings.TIME_ZONE` (`Europe/Athens`), the current timezone of
every request; `TruncMonth` and the year bounds read it, so midnight Athens time
starts the next month and a year (DST included).

### What a sale is worth

The goods only, and the same numbers as the receipt's goods lines:

```
line net = round_half_up((price * quantity - discount_allocation) / (1 + vat_rate / 100), 2)
```

`vat_rate` and `discount_allocation` are the snapshots on each `OrderItem`
(docs/order-system.md section 3.5), so editing a VAT row or a promotion later
changes nothing. This is `order.taxation.split_gross` applied to the `GOODS`
lines of `order.taxation.order_vat_lines`, written as SQL.

- **VAT** is excluded at each line's own rate, so mixed rates are exact.
- **Shipping and the payment-method fee** are separate receipt lines
  (`ChargeKind.SHIPPING`, `PAYMENT_FEE`) and are left out. The refund
  adjustment below keeps a refund that paid shipping back from being counted as
  goods.
- **Promotions, coupons and the loyalty redemption** reduce the net: they are
  allocated on the lines they were granted on.
- **A gift card** is a payment instrument, never an allocation
  (`order.discounts`), so an order paid by gift card is turnover in full. Selling
  the card is not an `Order` and is not turnover; the value shows up when it is
  redeemed.

### Returns

Money that went back, in the month it went back (not the month of the sale):

| Source | Month | Sign |
|---|---|---|
| `PaymentTransaction` kind `REFUND` (provider refund, or the merchant's own cash repayment) | `created_at` | + |
| `PaymentTransaction` kind `REFUND_REVERSAL` (a refund the provider reported failed) | `created_at` | - |
| `GiftCardTransaction` kind `REFUND_CREDIT` (value returned to a gift card, including a cancel) | `created_at` | + |
| `PaymentTransaction` kind `CHARGEBACK` with a `ChargebackDecision` `goods_returned` | `decided_at` | + |

These are the facts the myDATA credit documents are created from
(`order.credit_documents.create_pending_credits`). A chargeback is a return only
when staff decided the goods came back; a lost dispute for goods the customer
kept leaves the sale standing, as it does for the receipt. Points restored are
not counted: a loyalty redemption is a discount already taken off the sale, so
the money and gift-card shares of a full return add up to the discounted receipt.
A return whose order is not a sale (`payment_status` not in
`SALE_PAYMENT_STATUSES`) is not counted, so a canceled unpaid order that gave a
gift card back is neither.

A refund is gross money, not a list of lines, and may include shipping. It is
brought to net goods pro rata over the receipt, the way a credit document
spreads a refund over the receipt's rates:

```
returned net = round(amount * order goods net / receipt gross, 2)
receipt gross = goods after discounts + shipping + payment-method fee
```

Refunding the whole receipt returns exactly the sale's net goods; a partial
refund returns its share, rounded to the cent per refund, so several partial
refunds of one order can differ from the receipt's cumulative credit by a cent.
The receipt's own cumulative rounding (`credit_breakdown`) needs the receipt
rows and is not reproducible in a set-based query.

## Where it shows

Store admin, `Sales -> Turnover` (`admin:turnover`, `admin/turnover/`): four
totals for the year, a table of twelve months with a total row, a year selector
(the oldest sale's year to this year) and the CSV download (`admin:turnover-csv`,
`admin/turnover/export/?year=`). Empty months are zeros. `?year=` outside the
offered years is a 400.

The permission is `order.view_turnover_report`, declared on `Order.Meta` and
granted by `tenant.auth_backends` to store ADMIN and OWNER (a custom permission
goes to the roles that get the full action set) and to platform superusers.
STAFF get 403 and no sidebar entry. The store's takings are not the day-to-day
operations STAFF run; granting it to STAFF means adding the codename to
`_permissions_for_role` in `tenant/auth_backends.py`.

The CSV has plain figures (`1234.50`, ISO months `2026-10`), a UTF-8 byte-order
mark so a spreadsheet reads the Greek headings, and a total row.

The platform admin has no cross-store view: a store's figures are read inside
its own schema, and a platform view would have to switch into every schema per
request (the reason `TenantStatsSnapshot` exists). It is not built.

## Performance

Five aggregate queries for a year however many orders there are
(`tests/unit/order/test_turnover.py::test_the_query_count_does_not_grow_with_the_orders`):
sales per month over `OrderItem`, orders per month over `Order`, and one query
each for refunds, gift-card credits and chargebacks. Filters use existing
indexes (`payment_status, -created_at` on orders, `created_at` on the ledger).
The figures are not cached or snapshotted: the page is opened rarely, a closed
month only changes when a cash-on-delivery order placed in it is collected
later, and a snapshot would have to be invalidated by exactly that.

## Tests

`tests/unit/order/test_turnover.py` (definition, rounding, states, refunds,
timezone edges, query count), `tests/integration/admin/test_turnover_page.py`
(permission, page, CSV, year, sidebar), `tests_mt/test_turnover_isolation.py`
(one store's sales are not in another's figures).
