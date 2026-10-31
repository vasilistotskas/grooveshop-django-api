# Management profit report

What the store earned after what it cost to earn it, for the store OWNER only.
It is **management profit**, not the statutory or tax profit (ν.4308/2014): that
comes from the books (opening stock plus purchases less closing stock, payroll,
depreciation, financial items), none of which the platform records. Source:
`order/profit.py` (the definition and every query), `admin/profit.py` (the page
and the two exports), `core/templates/admin/profit.html`,
`order/models/expense.py` and `order/models/item.py` / `payment_transaction.py`
(the inputs), `order/payment_fees.py` and `order/views/viva_webhook.py` (the
provider fees), `tenant/role_scopes.py` (who may see it). The code wins over
this page.

## The definition

```
  net revenue                      order.turnover: sales less returns, net of VAT
- cost of goods sold               units that shipped, less units restocked
= gross profit
- payment fees                     what the providers charged
- delivery costs                   expenses of the SHIPPING category
= contribution
- other expenses                   every other expense category
= operating profit
```

Every figure is the store's own record. Nothing is estimated, and what is not
recorded stays visible instead of reading as zero.

### Net revenue

`order.turnover` (`docs/turnover-report.md`): the same sales, returns, shipping
and fees in the same periods, so the two reports cannot disagree. A sale is
earned when the order leaves the store (`Order.shipped_at`).

### Cost of goods sold

- **Sold:** `OrderItem.unit_cost x quantity` of the lines whose order shipped in
  the period.
- **Restocked returns:** less `OrderItem.unit_cost x StockLog.quantity_delta` of
  the restocks `OrderItem.refund` made (`StockLog.order_item`), in the period of
  the restock. A return that was not restocked is a write-off and keeps its cost.
  A cancel restocks by product, not by line, and names no line, so it never
  reverses a cost.
- A line sold with **no recorded cost** (`unit_cost` is null) adds nothing and
  is reported as `goods_uncosted`. A cost of zero is a recorded cost.

`OrderItem.unit_cost` is copied from `Product.cost_price` by `OrderItem.save`
when the line is created (every creation path goes through it: checkout, a
promotion's gift line, the admin, a script), exactly like `vat_rate`. Editing
the product's cost later never restates a sale. A caller that states a cost
keeps it. Lines sold before the field existed are null and are never backfilled:
a cost copied onto a past sale would be a guess.

**Cost coverage** is `goods_costed / (goods_costed + goods_uncosted)` over the
net goods sold (the `GOODS` `OrderVatLine` rows, split by whether their item has
a cost). Until it is 100% gross profit, contribution and operating profit are an
upper bound, and the page says so.

### Payment fees

`PaymentTransaction.fee`, summed in the period of the row (`created_at`).
`fee_source` says which provider report it came from
(`PaymentFeeSource`); a fee and its source are set together and a fee is never
negative (a CHECK). Null means "not reported": it adds nothing and lowers the
**fee coverage**, the share of online payments (CHARGE rows) by value with a
fee.

- **Stripe** (`order/payment_fees.py`): the `fee` of the charge's balance
  transaction, reached through dj-stripe (`Charge.sync_from_stripe_data`,
  `Charge.balance_transaction`). Written by `handle_stripe_charge_fee` on
  `charge.succeeded` and `charge.updated`, and by
  `record_stripe_fee_for_intent` after `payment_intent.succeeded` books the
  payment, because Stripe orders its events freely. A fee settled in another
  currency than the payment is left unreported.
- **Viva** (`_handle_transaction_price_calculated`): event 1799 *Transaction
  Price Calculated*, field `TotalCommission` (interchange + ISV fee + Viva's own
  fee, major units of `CurrencyCode`). `Interchange` is not read: the sample in
  Viva's documentation gives it a negative sign next to a positive total.
  Verified like every Viva event (Retrieve Transaction with the store's
  credentials, one of the order's own order codes), deduplicated on
  `VivaWebhookEvent`. Viva does not say whether 1799 comes before or after
  1796 (the payment webhook that books the charge), so a commission for a
  payment the ledger does not hold yet is kept in `PendingPaymentFee` and
  written onto the row when `PaymentLedger` records it (in `_record`, for
  every path that books a payment). The event is then answered `200` and
  never needs a retry. A commission whose transaction cannot be verified, or
  belongs to another order, is kept nowhere. **Each store must subscribe the
  event** in its Viva dashboard; not subscribed means "not reported".

  Subscribe it once per store that takes Viva payments (Viva's webhook dialog
  takes one event type per entry; the same URL serves every event):

  1. In the live Viva account, open Settings > API Access > Webhooks.
  2. Click Create Webhook.
  3. Enter the store's webhook address, the same one already used for
     Transaction Payment Created (the store host followed by
     `/viva-wallet/webhook/`).
  4. Click Verify and wait for the confirmation.
  5. Choose the event type Transaction Price Calculated.
  6. Tick Active and save.
  7. After the next card payment, open the profit report: that payment shows
     a Viva fee instead of "fee not reported".

  Only the documented payload was available when it was written
  (developer.viva.com/webhooks-for-payments/transaction-price-calculated): check
  the first real 1799 of a store (its `VivaWebhookEvent` row and the logged
  `CurrencyCode`, `TotalCommission`, `Interchange`, `IsvFee`) and whether a
  refund carries a commission event of its own.

### Delivery costs and other expenses

`Expense` rows (`amount_net`, by `Expense.date`). A carrier's invoice is the
`SHIPPING` category (`DELIVERY_EXPENSE_CATEGORIES`); everything else is an
operating expense. VAT on an expense (`vat_amount`) is kept for the accountant
and never counted.

### A breakdown

By product, category, payment method, channel or VAT rate: goods only, before
returns, over the lines that carry a cost (`GroupProfit`). Fees, returns and
expenses belong to the store or the order, not to a line, so they are not split.

## Where it shows

Store admin, `Sales -> Profit` (`admin:profit`), the filters and exports of the
turnover page (`start`, `end`, `bucket`, `compare`, `group`), with a CSV and an
XLSX export. `Sales -> Expenses` is the model's own admin page.

## Who sees it

The OWNER only (`tenant.role_scopes.OWNER_ONLY_PERMISSIONS`, applied in
`tenant.auth_backends._permissions_for_role`): `order.view_profit_report`, the
four `Expense` permissions and `product.manage_cost_price`. ADMIN keeps every
other store permission, including the turnover. STAFF are denied them too.
Platform superusers pass every check as usual.

`Product.cost_price` is hidden the same way: `ProductAdmin.get_fieldsets` adds
it to the form only for a holder of `product.manage_cost_price` (a crafted POST
cannot write a field the form does not have), it is in `export_exclude_fields`
(the admin's CSV/XML export), it is excluded from the product history table
(`COST_PRICE_FIELDS`, which the history page shows to anyone who can change the
product), and it is in no serializer. `OrderItem.unit_cost` is `editable=False`
and in no serializer or fieldset. An admin that reads a deferred money field
must load it up front: django-money reads the amount straight from the instance
dict (`ProductAdmin.get_queryset`).

## Performance

Five aggregate queries on top of turnover's, a constant number however many
orders there are (`tests/unit/order/test_profit.py`).

## Tests

`tests/unit/order/test_unit_cost_snapshot.py` (the snapshot),
`tests/unit/order/test_profit.py` (the definition, coverage, restocks, query
count), `tests/integration/order/test_payment_fees.py` (the ledger fee, Viva
1799, Stripe), `tests/integration/admin/test_profit_inputs_permissions.py`
(who sees what, the expense admin, the cost price on the product page),
`tests/integration/admin/test_profit_page.py` (the page and the exports).
