# Order system: what an order records when it is created

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 3.4 Acquisition source (`src/order/attribution.py`)

Every order created by either checkout path gets exactly one
`OrderAttribution` row (`src/order/models/attribution.py`, one-to-one,
`related_name="attribution"`), written by `OrderAttributionService.record`
(`src/order/services.py`) right after `Order.objects.create` inside the
creation transaction. A checkout order with no signal at all gets a
`direct` row, so for checkout orders **no row** means placed before the
table existed (`0059_orderattribution`, no backfill), never "direct".
Orders made outside checkout — the demo seed (`src/devtools/demo_account.py`,
`bulk_create`) and the test factories — have no row either.

- **Input.** `OrderCreateFromCartSerializer.attribution`
  (`OrderAttributionInputSerializer`, write-only, optional): `utm_source`,
  `utm_medium`, `utm_campaign`, `click_ids` (parameter NAMES from
  `CLICK_ID_PARAMS`, never values), `referrer`, `landing_path`, and
  `agent_protocol` (`ucp`/`acp`/`mcp`). The agent gateway sets it, and
  `OrderAttributionService.input_from_request` (`src/order/services.py`)
  believes it only when the request also carries the
  gateway's shared secret, which it sends as `X-Internal-Gateway` on
  every cart and order call (`is_internal_caller`, `src/tenant/internal.py`);
  from anyone else it is dropped and the order is
  classified from its other signals.
  `input_from_request` adds the request's `User-Agent`,
  which the Nuxt order proxy relays from the shopper
  (`clientIdentityHeaders`). Content is never refused, only shape: a
  checkout must not fail over a strange referrer.
- **Classification.** `classify()` is pure (no ORM, no request).
  First match wins: agent protocol → `utm_source` (`paid` when
  `utm_medium` matches GA4's paid rule or a paid click id is present,
  else `campaign`) → click id (`fbclid` is `social`, the rest `paid`;
  an Instagram in-app UA turns `fbclid` into `instagram`) → known
  referrer host (`KNOWN_SOURCES`) → in-app browser UA marker → any other
  external referrer (`referral`, the host as source) → `direct`. The
  store's own domains (`tenant_domain_set`) are never a referrer, so a
  return from a hosted payment page cannot become a source.
- **Stored.** `source_type` (`OrderSourceType`,
  `src/order/enum/attribution.py`),
  `source` (a registry key, the referrer host or the raw `utm_source`;
  empty for direct), `medium`, `campaign`, `referrer_host` (host only,
  no path or query), `landing_path` (path only). Every column is capped
  in `classify()`, not by the serializer.
- **Read.** `OrderSerializer.attribution` (`source_type`, `source`,
  `medium`, `campaign`; null without a row) on list and detail, loaded by
  `OrderQuerySet.with_attribution()`. API filters on `/order`:
  `source` (iexact on `attribution__source`) and `sourceType`
  (`src/order/filters.py`).
- **Admin.** `OrderAdmin.order_source` (badge coloured by
  `ORDER_SOURCE_TYPE_VARIANT`, `src/admin/displays.py`), the
  `attribution__source_type` and `OrderSourceFilter` filters, six
  read-only `attribution_*` fields in the "Additional Information" tab,
  and the dashboard's "Where orders come from" card
  (`src/admin/dashboard/store/queries.py: order_sources`, drawn by
  `widgets.py: StoreOrderSources`; the dashboard's selected period, top
  five as shares plus "Other", orders without a row left out).
  `source_label()` turns a stored `(source, source_type)` into the name
  staff read.
- **Trust.** Every input but the agent protocol is shopper-controlled,
  so the row is analytics only and never an authorisation or pricing
  signal. The agent source type is the one verified claim (see Input).
- **Cart first touch (`cart.CartAttribution`).** The cart records where
  its shopper came from too, because that is often not where they
  checked out ("checkout from Google, cart started from Instagram").
  `OrderAttribution` and `CartAttribution` share their columns through
  the abstract `AcquisitionSourceModel` (`src/order/models/attribution.py`).
  `POST /cart/item` takes the same optional write-only `attribution`
  object as order create (`OrderAttributionInputSerializer`, the same
  shape and rules, `agentProtocol` believed only with the gateway
  secret). `CartAttributionService.capture` (`src/cart/services.py`) is the
  one entry point, called BEFORE the line is written, by every path that
  can take a cart from no lines to some: the add itself (a new cart, or a
  refill after the shopper emptied it), `OrderService.reorder_to_cart`
  (the request's own signals: a reorder carries no capture) and the
  guest-to-account `merge_carts`. An empty cart takes the touch,
  replacing an earlier fill's; a cart holding lines keeps its own, so a
  second add never overwrites it; the merge carries the guest cart's
  touch over only when it is earlier than the account cart's (or the
  account cart has none). An add with no `attribution` object is
  classified like an order with no signal: from the shopper's
  `User-Agent` (an in-app browser names itself there), else `direct`.
  So every cart filled since `0015_cartattribution` has a row, the agent
  gateway's and the storage-blocked storefront's included, and **no row
  means only** a cart from before that migration (2026-10-17, no
  backfill: the first touch was never stored) or one that never held a
  line, never "direct". A guest cart from before it that is merged into
  an account cart brings no source, so the merged cart has none either.
  The cart response does not expose it; the storefront never reads it.
- **Cart source on the order.** The cart is deleted once its lines
  become an order (`_clear_cart_for_order`), so both creation paths copy
  the cart's first touch into `OrderAttribution.cart_source_type` /
  `cart_source` (`OrderAttributionService.record(..., cart=cart)`), next
  to the checkout source on the same row. Empty strings mean the cart
  had none recorded, not "direct". Not exposed on the order API.
- **Cart admin.** Carts show an "Order source" column and filters
  (`attribution__source_type`, `CartSourceFilter`), built on the same
  `AcquisitionSourceFilter` (`src/admin/filters.py`) as `OrderSourceFilter`,
  and a "Checkout" filter ("Checkout started, awaiting payment") for a
  live cart whose checkout already created an order in
  `awaiting_online_payment_q`. The order is found through
  `metadata["cart_snapshot"]["cart_uuid"]` in one correlated subquery on
  the changelist queryset. There is no column for it: the state lasts
  only while the hosted payment is open, so a column was blank on
  nearly every row. The filter is named apart from the idle "Abandoned"
  cart type. The order list adds a "Cart source"
  column, filters (`attribution__cart_source_type`,
  `OrderCartSourceFilter`) and change-form fields next to the order
  source: conversion by cart source is read from the orders list.
- **Erasure.** Both rows store a channel name, a medium, a campaign, a
  referrer host (no path or query) and a landing path (no query), all
  capped. They hold no account identifier, but `source`, `medium`,
  `campaign` and `landing_path` are whatever the shopper's link carried,
  so a hand-typed link can put personal text in them; nothing scrubs
  that. Erasure treats them as follows. `CartAttribution` cascades with
  its cart, and so with the account. `anonymise_and_delete_user` does
  not touch `OrderAttribution`: the order is kept as the store's
  record, and its attribution row stays with it, `source`,
  `landing_path` and the copied `cart_source_type` / `cart_source`
  alike (`test_the_cart_source_goes_with_the_cart_and_the_order_keeps_its_own`
  pins this). Neither row is in the data export.

Adding a known channel is one `KnownSource` entry (and, for a new
click-id parameter, one `CLICK_ID_PARAMS` entry, which changes the
generated `ClickIdsEnum` the storefront's capture is typed against —
regenerate the schema on both sides).

## 3.5 VAT on an order (`OrderItem.vat_rate`, `src/order/taxation.py`)

Prices on an order are VAT-inclusive. The VAT inside them is backed out
at **the rate the line was sold at**, which the order records and the
catalogue can no longer change.

- **The snapshot.** `OrderItem.vat_rate` is written when the line is
  created. Both checkout paths pass `cart_item.vat_percent` (the rate the
  cart backed the line's price out of, wholesale prices included), a
  promotion gift line passes its product's, and `OrderItem.save()`
  snapshots the product's current rate for every other writer (the order
  item admin and API, scripts); `bulk_create` skips `save()`, so
  `src/devtools/demo_orders.py` passes it itself. The column has
  `db_default=0` only so the release still serving during a rollout can
  keep inserting. Every product has a `Vat` row (`Product.vat` is NOT
  NULL, see `docs/product-pricing.md`), so no line is sold at a missing
  rate.
  The item admin cannot add a line (§3.7) and keeps the rate editable, so
  staff can correct a wrong snapshot, until the order's invoice carries a
  myDATA MARK; from then on it is read-only, because the registered
  document can no longer change. A correction rewrites the order's
  `OrderVatLine` rows (`write_vat_lines`), which the turnover report
  reads.
- **Nothing reads `product.vat` for an order again.** `src/order/invoicing.py`
  and `src/order/mydata/builder.py` get their lines from
  `order.taxation.order_vat_lines`, which reads `item.vat_rate`. Editing
  a `Vat` row, or moving the product to another, changes neither the PDF nor
  the myDATA payload of an
  existing order. Regenerating a not-yet-registered invoice therefore
  keeps the original rates too. Lines that predate the column were
  backfilled (`0065`) from their product's VAT at deploy time, which is
  what the documents used before the snapshot existed.
- **Where a discount lands.** A monetary discount (promotions + the
  loyalty redemption) reduces the taxable value of the lines it was
  granted on, not of every line: a coupon for the 13% food category must
  not be booked against a 24% line. `PromotionEngine` reports it:
  `PromotionEngine._clamp` splits each applied promotion over the cart
  lines it covers (`_covered_weights`: the lines `_matching_items` counts,
  by value, or the units a buy-X-get-Y actually discounts, by price) with
  `order.discounts.allocate_capped`, the one largest-remainder rule (cent
  exact, no line pushed below what is left of it), and exposes the result
  as `AppliedPromotion.line_amounts` / `CartDiscountResult.line_discounts`
  (cart item pk -> amount). The cart preview (`Cart.vat_after_discount`),
  the payment-intent amount and order creation all read the same
  evaluation. Checkout (`OrderService._snapshot_line_discounts`, both
  paths) freezes each line's share on `OrderItem.discount_allocation`
  (the whole line, not per unit) exactly as it freezes `vat_rate`; the
  loyalty redemption is order-wide and is spread over the value left on
  every line (`spread_by_remaining_value`; the standalone
  `/loyalty/redeem` endpoint does the same on an existing pending
  order). Gift-card amounts are payment and never allocated; a staff-added
  line carries none; a free gift has no value to carry any.
  `order.discounts.discounted_line_gross` reads the snapshots and raises
  when they do not add up to the order's discount (capped at its goods): a
  document on such lines would bill a total the customer never owed. That
  is the state of an order created by the previous release during the
  rollout of the column, or one whose discounted line staff deleted.
- **Orders without a snapshot.** `order` 0072 adds the column
  (`db_default` 0, currency column added with the `docs/migrations.md`
  money recipe) and 0073 gives every existing discounted order the
  proportional allocation (`heal_discount_allocation` in
  `src/order/discount_snapshot.py`): a `PromotionRedemption` row records the
  promotion and its amount but not the lines, and the promotion may have
  been edited since,
  so proportional, the rule every existing document was issued under, is
  the only honest reconstruction. `Invoice` rows are never touched, and
  one with a MARK is frozen anyway. Orders the previous release creates
  while it still serves the rollout are not covered by 0073 (it already
  ran): run `manage.py heal_discount_allocation --all-tenants` once after
  the last old pod has exited (idempotent: an order whose lines carry any
  allocation is skipped). The invoice task fails loudly on such an order
  until then and retries.
- **Shipping and the payment-method fee.** Incidental costs the seller
  charges the customer belong to the taxable amount of the goods they
  accompany (VAT Directive 2006/112/EC art. 78(b); Greek VAT Code, Law
  5144/2024, art. 24(4)(a) — art. 19 of the repealed Law 2859/2000), so
  they take the goods' rate. A basket that mixes rates has no single
  rate for them: each charge is split over the rates in proportion to
  the value of the goods charged at each, i.e. after their discounts
  (`_rate_weights` over `discounted_line_gross`; AADE circular Εγκ.
  10/1987 Π.4336/3162: incidental costs follow "την αξία των αγαθών";
  units when the goods are free; largest remainder, so the shares add
  up to the cent) and every share is taxed at its own rate. A
  single-rate basket puts 100% on that rate. There is no order-level
  "shipping VAT rate" to store: the split is a function of the
  snapshotted lines and the two amounts, and a stored copy could only
  disagree with the lines when staff edit them. A charge on an order
  with no goods (only reachable by staff deleting every line) raises,
  since no rate can follow nothing and a guessed one would be a wrong tax
  document. Every caller already surfaces it: `generate_order_invoice`
  logs and retries, the admin issue/regenerate actions show the
  message, and `submit_document` records a `BUILD` failure.
- **One computation, two documents.** Every line is split into a net
  rounded half-up to the cent and the VAT left of its gross, so each line
  adds up to what was charged, then summed
  (`order.taxation.split_gross`). `Invoice.subtotal`, `.total_vat`,
  `.total` and the PDF's VAT breakdown are sums of those lines, and the
  myDATA `invoiceDetails` / `invoiceSummary` are the same lines, so they
  agree to the cent for any order
  (`tests/unit/order/test_invoice_mydata_agreement.py`). The cart's
  `vat_after_discount` rounds the same way. `Invoice.shipping` and
  `.payment_fee` are the gross amounts, printed as reference rows;
  net + VAT is the whole document.

## 3.6 Legal acceptance (`OrderLegalAcceptance`, `LegalDocumentVersion`)

The buyer accepts the store's terms of use and privacy policy by
ordering (`CHECKOUT_CONSENT_SLUGS` in `src/page_config/legal_documents.py`).
`OrderCreateFromCartSerializer.terms_accepted` (write-only boolean) says
so; `false` is refused with `terms_not_accepted`. With it, the view
resolves the acceptance (`OrderLegalAcceptanceService.accept`) before it
creates anything or looks up a payment, and both creation paths store it
(`OrderLegalAcceptanceService.record`, `src/order/services.py`) right after
the attribution, inside the creation transaction: one
`OrderLegalAcceptance` row per document.

- **Which text.** `LegalDocumentVersion.objects.in_force(slug, language)`
  (`src/page_config/models.py`) snapshots the `ContentPage` translation in the
  order's language (`active_language_code`, what `Order.language_code`
  stores), exactly that language and only while the page is `published()`
  (a page scheduled for later is hidden), as the storefront's legal routes
  render it. A version is the title and body keyed by `legal_text_hash`
  (SHA-256 of the JSON pair, so no separator can move between them):
  reused while the text stays the same, a new row once the merchant edits
  the page, and never changed or deleted (`PROTECT` from both the
  acceptance and the page). The admin shows the order's acceptances inline,
  and every version read-only under Content → Accepted legal texts.
- **How.** `source` is `agent_checkout` when the request carries the
  agent gateway's shared secret (`X-Internal-Gateway`,
  `is_internal_caller`), whose UCP/ACP checkouts link both documents for
  the agent to present; any other caller is `checkout_consent`, the
  storefront's ticked checkbox.
- **No text, no order, no payment.** A document with no published,
  non-empty text in the language raises `LegalDocumentUnavailableError`:
  the buyer was shown a broken link, so nothing was accepted. Order
  creation answers 409 `legal_document_unavailable` (`error.document`
  names the slug), logs an error and creates nothing. The cart's
  `create_payment_intent` refuses the same way, because on the
  payment-first path ([§3.1](creation.md)) the card is confirmed before the
  order is
  created: no money moves for an order Django would refuse.
  A store does not get there by an edit: `src/page_config/legal_guard.py`
  refuses, in the admin and the staff API, unpublishing, scheduling,
  renaming, emptying a served language or deleting a legal page.
- **Every order.** `terms_accepted` is required, and both creation
  paths take the resolved `LegalAcceptance` as a required argument, so
  no order is created without both rows. Orders placed before the field
  existed (`0092_legal_acceptance`, no backfill) have none.

## 3.7 A placed order's lines are a record (`OrderItem.product_name`, `OrderItem.sku`)

A line's product, quantity and price decide `amount_due`, the VAT lines the
turnover report reads (`OrderVatLine`, written once at checkout by
`order.vat_snapshot`), the invoice and the stock taken. Nothing recomputes
those when a line is edited, so no admin surface edits one:

- **The admin.** `OrderItemInline` is read-only, with no add and no delete
  (`max_num = 0`, `can_delete = False`). `OrderItemAdmin` shows the product,
  quantity, price and the restock counters read-only, and offers neither add
  nor delete; staff can still annotate a line and correct its VAT rate (§3.5).
  The admin has no "Add order" (`OrderAdmin.has_add_permission`), and an
  order's amounts are read-only on its form (`shipping_price`, `amount_due`,
  like the discounts and the gift-card amount; `payment_method_fee` is not on
  the form): an order is placed by checkout, which prices it, takes the
  stock, reserves the discounts and writes the VAT lines, and nothing
  recomputes any of that when one number is edited. The one change made to a
  line after checkout is a restock (`OrderItem.refund`, the order's "Restock
  returned items" action, or `POST order-items/<id>/restock`).
- **The API.** `order-items` is read-only: `GET` list and detail and the
  staff restock remain; `POST`, `PUT`, `PATCH` and `DELETE` are gone (they
  could swap a line's product or change its quantity, leaving the snapshot,
  `amount_due`, the VAT lines and the stock apart). Nothing called them (the
  storefront, the gateway, the admin and the infrastructure scripts were
  searched); the removal is accepted in `api-contract/ignore.txt` until the
  storefront regenerates its types.
- **The snapshot.** `OrderItem.product_name` (the product's name in the
  ORDER's language, `Order.language_code`, else any language it has one in)
  and `OrderItem.sku` are written when the line is created, like `vat_rate`
  and `unit_cost` (`OrderItem.save()` fills them for every writer that does
  not state them; `bulk_create` skips `save()`, so it passes them itself).
  The invoice (`order.invoicing._render_items`), the order emails
  (`{{ item.product_name }}`), the admin, the order-item API
  (`productName`, `sku`) and its `product__name` / `product__sku` filters
  read the snapshot, never the product, so renaming or re-coding a product
  cannot rewrite a receipt, an email or a history already issued. The
  nested `product` object of the order API is still the live product (the
  storefront links to it); a consumer that prints the name of a placed
  line reads `productName`.
- **The columns.** NOT NULL with `db_default=''` from the start
  (`order` 0100), so nothing is tightened in a second release. Migration
  0101 copies the current product into every existing line;
  `manage.py backfill_line_snapshots --all-tenants` (the same idempotent SQL,
  `src/order/line_snapshot.py`) covers the lines the previous release creates
  while it still serves the rollout, whose INSERTs leave the columns at `''`.
  Run it **immediately after the rollout**, once per deploy that ships the
  columns: until it has run, a confirmation, payment or shipping email for
  a line the previous release wrote (an order placed in the rollout window)
  prints a blank product name, and its invoice a blank name and SKU. There
  is no fallback to the live product: the gap is closed by the command,
  never by reading the product again. The name is chosen as a new line
  chooses it (`product_name_for`): the order's language, then the next
  language of its parler fallback chain, then any language the product has.
