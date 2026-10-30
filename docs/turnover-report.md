# Turnover report

A store's turnover for any range of days: the goods **and** the shipping and
payment-method fee it invoices, net of VAT, an order counted when it left the
store, less what went back. One definition serves the turnover page, its CSV and
XLSX, and the store dashboard's revenue and best sellers. Source:
`order/turnover.py` (the definition and every query), `order/vat_snapshot.py`
(what it reads and how existing orders are filled in), `order/models/vat_line.py`
(`OrderVatLine`), `order/models/order.py` (`Order.shipped_at`, stamped in
`Order.save`), `admin/turnover.py` (the page and the two exports),
`core/templates/admin/turnover.html`, `admin/dashboard/store/queries.py` (the
dashboard reads `figures_between`), `admin/admin.py: MyAdminSite.get_urls` (the
three routes), `admin/navigation.py: store_sidebar` (the entry). The code wins
over this page.

## The definition

`net turnover = goods + shipping + fees - returns`, all without VAT, per bucket
(quarter, month, week, day, or the whole range), in the store's currency. VAT and
gross (`net turnover + VAT`) are reported beside it.

### When a sale is earned

The moment the order **left the store**: `Order.shipped_at`, for every payment
method alike. `Order.save` stamps it the first time the order is in a status of
`DISPATCHED_STATUSES` (`SHIPPED`, `DELIVERED`, `COMPLETED`, `RETURNED`,
`REFUNDED`), whichever path put it there (the service, the admin form, a carrier
event, a script): it is the one save they all share. A later status never moves
it.

Why dispatch and not payment or placement. The platform's retail receipt is
issued when the parcel ships (`order.invoicing.receipt_issuable`,
`docs/order-system.md` section 7.4), and a Greek store keeps accrual books
(ν.4308/2014): a sale belongs to the period of its receipt, not to the day a
courier pays a cash-on-delivery order out, nor to the day an online payment
cleared before the parcel left. The earlier rule (placed, and collected)
restated past months: on the production store 143 of 182 cash-on-delivery
collections landed in a later month than the order, up to 27 days later. The
rule was chosen by the owner on 2026-10-30; the statute's own wording of the tax
point was not checked against the law text when it was written, so confirm it
with the store's accountant before relying on the page for a filing.

- An order that never shipped is not a sale: a pending one is not yet, a
  canceled one (paid and refunded, or unpaid) never is.
- A sale is **not** conditioned on its payment. `COLLECTED_PAYMENT_STATUSES`
  (`COMPLETED`, `PARTIALLY_REFUNDED`, `REFUNDED`) is the only set a refund can
  follow; `AWAITING_PAYMENT_STATUSES` (`PENDING`, `PROCESSING`, `FAILED`) on a
  shipped order is cash on delivery in transit. Its `amount_due` is reported
  apart as `awaiting_collection` and falls out of it when the courier pays.
  Collecting changes no past bucket.
- A parcel the customer refused closes the order unpaid: `Order.save` settles
  `payment_status` to `CANCELED` on `RETURNED` (`Order.settle_unpaid_payment`).
  The sale stands in the month it shipped and is reversed, whole, in the month of
  the closure (`status_updated_at`, which on such an order is the `RETURNED`
  transition: verified against `OrderHistory` on production, 55 of 55).

### What a sale is worth

The order's `OrderVatLine` rows: goods net (one row per `OrderItem`, at the rate
and the discount snapshotted on it), shipping net and fee net (apportioned over
the goods' rates by value, largest remainder), and the VAT of each. They are
written once, when the order is created, by
`order.taxation.order_vat_lines` (`write_vat_lines`, called at the end of both
creation paths in `OrderService`), the same function the PDF receipt, the
`Invoice` row and the myDATA payload derive their lines from, so a figure cannot
disagree with the receipt. A zero-gross line has no row, as on the receipt. A
later change to a product, a VAT row or a promotion rewrites nothing.

- **Shipping and the payment fee are turnover** (art. 78(b) of Directive
  2006/112/EC: incidental costs are part of the taxable amount; they are separate
  lines on the receipt). They are in their own columns, so a reader can still
  take them out.
- **Promotions, coupons and the loyalty redemption** reduce the net on the lines
  they were granted on (`OrderItem.discount_allocation`).
- **A gift card** is a payment instrument, never an allocation
  (`order.discounts`): an order paid by gift card is turnover in full when it
  ships. Selling the card is not an `Order` and is not turnover.
- A manual edit of an order's lines in the admin after creation does not rewrite
  its rows, like it does not rewrite `amount_due`.

### Returns

Money that went back, in the month it went back:

| Source | When | Sign |
|---|---|---|
| `PaymentTransaction` kind `REFUND` | `created_at` | + |
| `PaymentTransaction` kind `REFUND_REVERSAL` (a refund the provider reported failed) | `created_at` | - |
| `GiftCardTransaction` kind `REFUND_CREDIT` | `created_at` | + |
| `PaymentTransaction` kind `CHARGEBACK` with a `ChargebackDecision` `goods_returned` | `decided_at` | + |
| A shipped order that closed unpaid (`payment_status` `CANCELED`), whole | `status_updated_at` | + |

These are the facts the myDATA credit documents are created from
(`order.credit_documents.create_pending_credits`). A chargeback is a return only
when staff decided the goods came back. Points restored are not counted: a
loyalty redemption is a discount already taken off the sale. A return whose
order never shipped is not counted (the sale it would reverse was never earned).

A refund is gross money, not a list of lines. It is brought to net and VAT pro
rata over the receipt, as a credit document does:

```
returned net   = round(amount * order net / receipt gross, 2)
returned VAT   = amount - returned net
receipt gross  = the order's OrderVatLine net + VAT
```

Refunding the whole receipt returns the whole sale, shipping and fees included
(to the cent per refund: several partial refunds of one order can differ from the
receipt's cumulative credit by a cent, because `credit_breakdown` needs the
receipt rows and is not reproducible in a set-based query).

### Buckets, comparison, breakdowns

`figures(start, end, bucket, group_by=None)` takes local days, both inclusive;
`figures_between(first, last, ...)` takes the aware span the dashboard needs.
Buckets are `Bucket.QUARTER`, `MONTH`, `WEEK` (from Monday), `DAY`, or `None` for
the range as one row. An empty bucket is a row of zeros. They follow
`settings.TIME_ZONE`, the current timezone of every request, so midnight Athens
time starts the next bucket and a year (DST included).

`figures_with_prior_year` runs the same range a year earlier; a prior bucket lines
up with a current one by `a_year_earlier(day)` (29 February becomes the 28th).

`group_by` (`GroupBy`: payment method, category, product, channel, VAT rate)
returns one row per group (and bucket), biggest first, as **sales before
returns**: a refund is not split by product, category or rate. Product and
category count goods lines only, because shipping and fees have no item. Payment
method is the order's `pay_way_key` snapshot, channel its `OrderAttribution.source`
(none: no source recorded; empty: a direct visit).

`units` is the quantity of goods lines that have a row (a free gift has none).
`average_order_value` is gross sales per order.

## Where it shows

Store admin, `Sales -> Turnover` (`admin:turnover`, `admin/turnover/`). Query
parameters, validated by `admin.turnover.FilterForm`: `start`, `end` (ISO dates),
`bucket`, `compare` (`year` or empty), `group`. Absent parameters take their
defaults (this year to date, by month, against last year, no breakdown); a
parameter that is present and empty is kept. The page shows eight totals, a bar
chart (`order.turnover` rows through `admin.dashboard.base.bar_chart`, Unfold's
`chart/bar.html`), the period table and the breakdown table. An invalid request
renders the form with its errors; a range that would draw more than
`MAX_PAGE_ROWS` rows is refused on the page only.

Exports share the page's columns (`admin.turnover._columns`) and its form, so
they answer 400 where the form refuses, and have no row cap:

- `admin:turnover-csv` (`admin/turnover/export.csv`): the periods, or the
  breakdown in long form (a row per bucket and group) when `group` is set. Plain
  figures (`1234.50`, ISO periods `2026-10`, `2026-Q4`, `2026-W41`), a UTF-8
  byte-order mark so a spreadsheet reads the Greek headings.
- `admin:turnover-xlsx` (`admin/turnover/export.xlsx`): the same on sheet one and
  the breakdown on a second sheet, written with `openpyxl` in write-only mode;
  money cells are numbers with a `#,##0.00` format.

The permission is `order.view_turnover_report`, declared on `Order.Meta` and
granted by `tenant.auth_backends` to store ADMIN and OWNER (a custom permission
goes to the roles that get the full action set) and to platform superusers.
STAFF get 403 and no sidebar entry.

Money is formatted with the store's currency (`admin.displays.money`, from
`Tenant.default_currency`; the platform default on a page that belongs to no
store). `Tenant.default_currency` offers only `settings.DEFAULT_CURRENCY`: every
money path works in it and nothing converts.

The platform admin has no cross-store view: a store's figures are read inside its
own schema.

## The dashboard

`admin/dashboard/store/queries.py` reads the same module: `overview` (revenue
and its prior period, orders sold) and `sales_series` (revenue per bucket) call
`figures_between`; `best_sellers` counts units of orders with `shipped_at` in the
period. Revenue there is net turnover. The cached queries depend on
`TURNOVER_MODELS` (the order, its VAT lines, the ledger, chargeback decisions and
gift-card transactions), so a refund clears them.

## Rolling it out

`order` 0094 adds `OrderVatLine` and `Order.shipped_at` (add only); `order` 0095
fills existing orders: VAT lines from the same `order_vat_lines` (on the
migration's historical models: `tests/integration/order/test_migration_turnover_snapshots.py`)
and `shipped_at` from the order's history (the first status change into a
dispatched status; an order with no such row takes its last status change, then
its creation). An order whose receipt cannot be derived is named and skipped,
never guessed. Orders the previous release creates or ships while it still
serves the rollout are not covered, because the migration already ran: run
`manage.py backfill_turnover_snapshots --all-tenants` after the rollout. It is
idempotent. The seeded demo orders (`devtools/demo_orders.py`) write both
snapshots, and a `REFUNDED` demo order gets its charge and refund ledger rows
(`devtools/demo_account.py: _seed_refund`).

## Performance

Three queries for a range however many orders there are, one per breakdown: the
sales over `OrderVatLine` joined to `Order` (filtered on `Order.shipped_at`,
indexed `order_shipped_at_ix`, then the lines through their `order` foreign key),
the orders and what is awaiting over `Order`, and one `UNION ALL` of the four
return sources (`tests/unit/order/test_turnover.py::TestQueries`). The figures are
not cached on the page: it is opened rarely.

## Tests

`tests/unit/order/test_turnover.py` (definition, rounding, basis, returns,
buckets, prior year, breakdowns, timezone edges, query count),
`tests/integration/order/test_vat_snapshot.py` (the snapshots, both creation
paths, the backfills), `tests/integration/order/test_migration_turnover_snapshots.py`,
`tests/integration/admin/test_turnover_page.py` (permission, form, page, CSV,
XLSX, sidebar), `tests/unit/admin/dashboard/test_store_queries.py` (the dashboard
reads the same figure), `tests_mt/test_turnover_isolation.py` (one store's sales
are not in another's).
