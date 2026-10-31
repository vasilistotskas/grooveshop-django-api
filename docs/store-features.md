# Store features

What a store can switch on or off, and how a client learns the answer.
Source: `tenant/features.py` (`STORE_FEATURES`, `feature_on`,
`effective_features`), `tenant/permissions.py` (the gates),
`tenant/serializers.py` (`StoreFeaturesSerializer`), `admin/features.py`
(the admin's registry), `admin/mixins.py` (`FeatureGatedModelAdmin`,
`FeatureInline`), `admin/navigation.py`. The code wins over this page.

## The rule

A feature is **on** when its plan flag on `Tenant` (if it has one) and
every store setting it needs are on. `STORE_FEATURES` names the same
permission classes that answer 404 for the feature, so what
`TenantConfig.features` tells a client can never disagree with what the
API serves. A client reads `features` and nothing else: it never combines
a plan flag with a setting, copies a setting's default, or keeps a
fallback for a setting it could not fetch.

- Off is a bare **404**, indistinguishable from a route that was never
  registered. Each refusal is logged as `feature_gate:refused`.
- The platform host is not a store: `effective_features` is all `false`
  there, and the gates never refuse (`feature_on` answers as the gates do).
- A dependency is expressed by listing the parent's gates in the child's
  entry (`account_reviews` lists `IsProductReviewsEnabled` and its own
  switch), so one table row carries the whole chain.
- Server code reads a store setting only through
  `core.store_settings.store_setting` (or `store_settings` for several at
  once). A store with no row for a setting reads the value it is declared
  with in `settings.EXTRA_SETTINGS_DEFAULTS`, so that table is the one place
  a default lives, including whether a gate fails open or closed. An
  undeclared name raises. `GET /api/v1/settings/public` returns every public
  key resolved the same way, so a client has no default of its own to keep.
  Those keys are store VALUES only: no feature or preference switch is
  public (`PUBLIC_SETTING_KEYS`, enforced by
  `tests/integration/core/test_settings_public_api.py`), so a client reads
  a switch from `TenantConfig` and nowhere else.
- Adding a feature: a permission class, a `STORE_FEATURES` row, a field on
  `StoreFeaturesSerializer`, a regenerated `schema.yml`, and a row in
  `SERVED_BY` in `tests/integration/tenant/test_store_features.py`.
  Removing or loosening a field of `features` is a two-release change,
  storefront first.

## Classification of every `*_ENABLED` setting

Two classes matter to a client:

- **(a) store feature**: something the store offers, with API behaviour
  that must refuse when it is off. Lives in `STORE_FEATURES`.
- **(b) presentation preference**: a UI choice with no API surface.
  Served in `TenantConfig.preferences` (`STORE_PREFERENCES` in
  `tenant/features.py`), so a client reads it from the tenant payload like
  a feature, never from a settings read that can fail. A switch purges the
  store's pages and the storefront's copy of the payload, as a feature
  switch does.

A third group is read by no client at all: **(s) server-side switch**,
consumed by tasks and integrations.

| Setting | Class | Notes |
|---|---|---|
| `PRODUCT_REVIEWS_ENABLED` | (a) `product_reviews` | |
| `ACCOUNT_REVIEWS_ENABLED` | (a) `account_reviews` | needs `product_reviews` |
| `BLOG_COMMENTS_ENABLED` | (a) `blog_comments` | needs the `blog` plan flag |
| `FAVOURITES_ENABLED` | (a) `favourites` | needs `catalogue` |
| `NEWSLETTER_ENABLED` | (a) `newsletter` | |
| `FEEDBACK_ENABLED` | (a) `feedback` | |
| `LOYALTY_ENABLED` | (a) `loyalty` | needs the `loyalty` plan flag |
| `PROMOTIONS_ENABLED` | (a) `promotions` | needs the `promotions` plan flag |
| `GIFT_CARDS_ENABLED` | (a) `gift_cards` | needs the `gift_cards` plan flag and `catalogue` |
| `B2B_WHOLESALE_ENABLED` | (a) `wholesale` | needs the `b2b` plan flag |
| `PRODUCT_SUGGESTIONS_ENABLED` | (a) `product_suggestions` | needs the `recommendations` plan flag and `catalogue` |
| `PRODUCT_ALERTS_ENABLED` | (a) `product_alerts` | needs `catalogue` |
| `PRICE_HISTORY_ENABLED` | (a) `price_history` | default on: it shows prices the store already published |
| `CONTACT_ATTACHMENTS_ENABLED` | (a) `contact_attachments` | fails closed |
| `CHAT_WIDGET_ENABLED` | (a) `chat_widget` | needs agent commerce, both tiers |
| `CART_ENABLED` | (a) `cart` | needs `catalogue` |
| `CATALOGUE_ENABLED` | (a) `catalogue` | `favourites`, `gift_cards`, `product_alerts`, `product_suggestions` and `cart` need it |
| `B2B_INVOICING_ENABLED` | (a) `b2b_invoicing` | no plan flag; refused by order validation (400, `invoicing_disabled`), not a 404 gate |
| `MOBILE_BOTTOM_NAV_ENABLED` | (b) `mobile_bottom_nav` | |
| `STICKY_ADD_TO_CART_ENABLED` | (b) `sticky_add_to_cart` | |
| `RECENTLY_VIEWED_ENABLED` | (b) `recently_viewed` | history is client-side |
| `PRODUCT_ASSURANCES_ENABLED` | (b) `product_assurances` | the product page's delivery / payment / returns box; each row hides itself without data |
| `DEMO_ACCOUNT_ENABLED` | (b) `demo_account` | demo credentials card on the sign-in page |
| `AGENT_COMMERCE_ENABLED`, `AGENT_HOSTED_PAYMENT_ENABLED`, `PRODUCT_FEEDS_ENABLED` | agent surface | folded into `TenantConfig` as `agentCommerceEnabled` and friends, enforced by the gateway; `chat_widget` builds on the first |
| `META_CAPI_ENABLED`, `MYDATA_ENABLED`, `ACS_DYNAMIC_PRICING_ENABLED`, `B2B_LOYALTY_ENABLED`, `LOYALTY_NEW_CUSTOMER_BONUS_ENABLED`, `LOYALTY_TIER_MULTIPLIER_ENABLED` | (s) | never read by a client |

`ACCOUNT_LOGIN_BY_CODE_ENABLED`, `MFA_*_ENABLED`, `DB_POOL_ENABLED` and the
other `settings.py` constants are deployment configuration, not store
settings.

## Features, gates and dependencies

| Feature | Plan flag | Settings (all on) | Gated in |
|---|---|---|---|
| `blog` | `blog_enabled` | | `blog/views/` (posts, categories, authors, tags) |
| `blog_comments` | `blog_enabled` | `BLOG_COMMENTS_ENABLED` | `blog/views/comment.py`, `blog/views/post.py` |
| `product_reviews` | | `PRODUCT_REVIEWS_ENABLED` | `product/views/review.py`, `product/views/product.py` |
| `account_reviews` | | `PRODUCT_REVIEWS_ENABLED`, `ACCOUNT_REVIEWS_ENABLED` | `user/views/account.py` |
| `favourites` | | `CATALOGUE_ENABLED`, `FAVOURITES_ENABLED` | `product/views/favourite.py`, `user/views/account.py` (`favourite_products`), `agent/views.py` |
| `newsletter` | | `NEWSLETTER_ENABLED` | `user/views/` subscription views (token links in sent emails stay open) |
| `feedback` | | `FEEDBACK_ENABLED` | `contact/views.py` |
| `loyalty` | `loyalty_enabled` | `LOYALTY_ENABLED` | `loyalty/views/loyalty.py` |
| `promotions` | `promotions_enabled` | `PROMOTIONS_ENABLED` | `promotion/views.py`, cart coupon actions |
| `gift_cards` | `gift_cards_enabled` | `CATALOGUE_ENABLED`, `GIFT_CARDS_ENABLED` | `giftcard/views.py` (a purchase's status stays open) |
| `wholesale` | `b2b_enabled` | `B2B_WHOLESALE_ENABLED` | `b2b/views.py` |
| `b2b_invoicing` | | `B2B_INVOICING_ENABLED` | `order/serializers/order.py` (`document_type=INVOICE` is a 400, through `feature_on`) |
| `product_suggestions` | `recommendations_enabled` | `CATALOGUE_ENABLED`, `PRODUCT_SUGGESTIONS_ENABLED` | `recommendation/views.py`, `search/views.py`; the order email, candidate rebuilds and weight learning through `recommendation.context.recommendations_active` |
| `product_alerts` | | `CATALOGUE_ENABLED`, `PRODUCT_ALERTS_ENABLED` | `product/views/alert.py` |
| `price_history` | | `PRICE_HISTORY_ENABLED` | `product/views/product.py` (`price_history`, `GET /product/{id}/price-history`) |
| `contact_attachments` | | `CONTACT_ATTACHMENTS_ENABLED` | `contact/views.py` (`contact-attachment`) |
| `chat_widget` | `agent_commerce_enabled` | `AGENT_COMMERCE_ENABLED`, `CHAT_WIDGET_ENABLED` | `/api/v1/agent/*` by the agent-commerce gates; the gateway's own `/chat` is enforced there |
| `catalogue` | | `CATALOGUE_ENABLED` | `product/views/` (products and their public actions, categories, category images, product images, brands, attributes, attribute values), `search/views.py` (`product_meili_search`, and `search_trending` for the product queries) |
| `cart` | | `CATALOGUE_ENABLED`, `CART_ENABLED` | `cart/views/` (every cart and cart-item action but the staff `cart/list`), `order/views/order.py` (`create`; listing, reading and paying existing orders stay open) |

`chat_widget` is the one feature whose consumer endpoint is not Django's:
the widget talks to the agent gateway, which refuses a store whose agent
commerce is off. Django states the widget's own switch in `features`; the
gateway does not read `CHAT_WIDGET_ENABLED`, so a store that hides the
widget still has a live `/chat` (flipping it back needs no deploy).

## Reviews and the star average

A store whose reviews are hidden also hides the star average and the
review count. While `product_reviews` is off, the product serializers
(`ProductSerializer` and its detail tiers) serve `reviewAverage: 0.0` and
`reviewCount: 0`, and the product page's `ratingDistribution` all zeros, what a product without reviews serves, and the search hit
serializer serves `reviewCount: 0` and `reviewAverage: null`. The fields
keep their shape: making a field nullable is a loosening that the live
storefront's schemas reject.

## Cart and catalogue: the move (done)

The cart and the catalogue started as two presentation preferences that
hid the storefront's chrome while their routes kept answering, and the
agent gateway calls those routes (`/cart`, `/cart/item`,
`/cart/reserve-stock`, `/order`, `/product`, `/product/category/all`).
They moved into `STORE_FEATURES` in four releases, in this order because a
client reads a field that is missing from `features` as off:

1. `features.cart` and `features.catalogue` were stated, beside the
   preferences. Additive.
2. The gateway read them and stopped offering the cart, checkout and
   catalogue tools, the feeds and the chat's product tools when off.
3. The storefront read them from `features`.
4. The routes now refuse (404, `feature_gate:refused`), and
   `preferences.cart` and `preferences.catalogue` left `STORE_PREFERENCES`,
   a field removal that only shipped once step 3 was in production.

What refuses when `catalogue` is off: the public product routes, their
actions, categories, category images, product images, brands, attributes,
attribute values, `search/product` and the product `search/trending`, plus
what only exists with a catalogue (favourites, gift cards, product alerts,
suggestions, each listing `IsCatalogueEnabled` first in its entry). What
refuses when `cart` is off: the cart and cart-item actions and order
create. A store without a catalogue has no cart either.

Who stays served. The gate is on the shopper-facing action, and the store's
own staff routes carry none: the staff cart listing (`cart/list`) and the
create, update and delete of the catalogue records (all
`StoreStaffModelPermissions`) answer whatever the switch says, so a store
can prepare a catalogue before it publishes one. A route that shoppers and
staff share (a product list, a cart-item list) refuses for everyone, the way
the blog gates do. Orders a store already has stay readable and payable
(list, detail, the payment and cancel actions, `viva_return`) with the cart
off; `GET /order` is the customer's history either way.

## Store languages

`Tenant.available_locales` is the list of languages a store's storefront
serves; empty means the default locale only. Platform staff set it on the
tenant form's Branding tab (`TenantAdmin.fieldsets`); a store operator sees
it read-only, because it is not in `TENANT_SELF_EDITABLE_FIELDS`. The field
accepts only `STOREFRONT_LOCALES` (the storefront's routes, kept apart from
`settings.LANGUAGES`, which names the content languages) and
must contain `default_locale`. Adding a language also needs that language's
legal documents to exist (`Tenant._validate_legal_documents_for_new_locales`).

A change purges what the storefront cached for the store: the language
switcher and hreflang set of every page, and the sitemap
(`purge_store_locales_task`: the `store_pages` and `sitemap_seo` surfaces).
It is queued once the change commits and held for
`STOREFRONT_TENANT_CACHE_SECONDS`, because the storefront reads the list from
its copy of the tenant-resolve payload and a purge before that copy expires
would re-cache pages rendered from the old list. A save that leaves the list
unchanged queues nothing.

## The admin

The store admin shows a feature by one rule: **a feature's surface is shown
when the store can use the feature, or when it holds records.** Nothing that
exists is made unreachable, nothing empty and unusable is shown, and a hidden
page is refused (403), not only unlinked. `admin/features.py` is the one
registry: `FEATURES` is `STORE_FEATURES` plus `retail_documents`
(`MYDATA_ENABLED`, a server-side setting no client reads), and
`FEATURE_SURFACES` names the model pages each feature owns.

A feature is in one of three states (`Availability`): `LIVE`, `SETTING_OFF`
(plan on, the store's setting off) or `UNAVAILABLE` (off the plan). It is
read once per request (`store_features(request)`; the settings in one
`store_settings` read), through the same plan flag and settings as the API.

| Surface | Shown | Off |
|---|---|---|
| **Setup** page (tiers, promotions, slots, products and catalogue pages), a product's loyalty-points and alert fields | the plan allows it; with the setting off only to a role that can switch it on (`extra_settings.change_setting`: OWNER, ADMIN) | "Off" badge; STAFF do not see it; off the plan, hidden |
| **Record** page (receipts, ledgers, subscriptions, orders, carts) | live, or the table has rows | "Off" badge; off the plan the rows are read-only (no add, change or delete); with only the setting off nothing new is added |
| **Per-object** surface: inline, fieldset, field, list filter | live, or this object (a filter: any row) has data for it | the order of an invoice keeps its Billing / Invoice fields, an order that was charged a loyalty discount keeps the field |
| **Action** that creates a record | the feature is live | refused |

How it is wired:

- `BaseModelAdmin` (via `FeatureGatedModelAdmin`) answers
  `has_module_permission` and the four object permissions from the model's
  placement, so the sidebar, the dashboard links and the ⌘K palette (all built
  from `has_module_permission`) and a typed URL follow the same predicate.
  `MyAdminSite.get_tabs_list` keeps a tab of `UNFOLD["TABS"]` only while its
  page opens, through Unfold's own per-tab `has_permission`.
- `feature_gates` on an admin lists `Gate(feature, fields=…, filters=…,
  kind=…, data=Q(...))`: the fields (an emptied fieldset goes with them) and the
  list filters hidden while the feature is not shown. `data` is the filter that
  says what "this object has data" means; without it, any row of the feature's
  own pages.
- `FeatureInline` (`feature = "…"`) gates an inline: Django already drops an
  inline with no permission, so its tab goes without touching `get_inlines`.
- A new feature-owned page is added to `FEATURE_SURFACES`; a new gated inline
  names its feature. `tests/integration/admin/test_feature_gating.py` walks
  the registry and fails for a placed model whose admin is not gated, a gate
  naming a field or filter the admin does not declare, and an inline of a
  feature's records that is not a `FeatureInline`.
- Two predicates for receipts, kept apart on purpose: `retail_documents` is
  `MYDATA_ENABLED`, when the pages are *shown* (an owner who switched myDATA on
  but is not ready still sees where receipts will appear); `MyDataConfig.is_ready`
  (environment, FHM ground, credentials) is when the platform can *issue*, and
  the order's "Invoicing & myDATA" actions ask it (`has_issuing_permission`).
