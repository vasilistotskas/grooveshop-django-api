# Store features

What a store can switch on or off, and how a client learns the answer.
Source: `tenant/features.py` (`STORE_FEATURES`, `feature_on`,
`effective_features`), `tenant/permissions.py` (the gates),
`tenant/serializers.py` (`StoreFeaturesSerializer`). The code wins over
this page.

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
- Adding a feature: a permission class, a `STORE_FEATURES` row, a field on
  `StoreFeaturesSerializer`, a regenerated `schema.yml`, and a row in
  `SERVED_BY` in `tests/integration/tenant/test_store_features.py`.
  Removing or loosening a field of `features` is a two-release change,
  storefront first.

## Classification of every `*_ENABLED` setting

Two classes matter to a client:

- **(a) store feature**: something the store offers, with API behaviour
  that must refuse when it is off. Lives in `STORE_FEATURES`.
- **(b) presentation preference**: a UI choice with no API surface. Stays
  a plain setting, read through one accessor by the storefront.

A third group is read by no client at all: **(s) server-side switch**,
consumed by tasks and integrations.

| Setting | Class | Notes |
|---|---|---|
| `PRODUCT_REVIEWS_ENABLED` | (a) `product_reviews` | |
| `ACCOUNT_REVIEWS_ENABLED` | (a) `account_reviews` | needs `product_reviews` |
| `BLOG_COMMENTS_ENABLED` | (a) `blog_comments` | needs the `blog` plan flag |
| `FAVOURITES_ENABLED` | (a) `favourites` | |
| `NEWSLETTER_ENABLED` | (a) `newsletter` | |
| `FEEDBACK_ENABLED` | (a) `feedback` | |
| `LOYALTY_ENABLED` | (a) `loyalty` | needs the `loyalty` plan flag |
| `PROMOTIONS_ENABLED` | (a) `promotions` | needs the `promotions` plan flag |
| `GIFT_CARDS_ENABLED` | (a) `gift_cards` | needs the `gift_cards` plan flag |
| `B2B_WHOLESALE_ENABLED` | (a) `wholesale` | needs the `b2b` plan flag |
| `PRODUCT_SUGGESTIONS_ENABLED` | (a) `product_suggestions` | needs the `recommendations` plan flag |
| `PRODUCT_ALERTS_ENABLED` | (a) `product_alerts` | |
| `CONTACT_ATTACHMENTS_ENABLED` | (a) `contact_attachments` | fails closed |
| `CHAT_WIDGET_ENABLED` | (a) `chat_widget` | needs agent commerce, both tiers |
| `CART_ENABLED` | (a), **not yet in the table** | see "Not yet gated" |
| `CATALOGUE_ENABLED` | (a), **not yet in the table** | see "Not yet gated" |
| `ACS_SMARTPOINT_ENABLED` | (a) option of a carrier | the shipping options response omits the locker option; no route to 404 |
| `B2B_INVOICING_ENABLED` | (a) option of checkout | enforced by order validation (400), not a 404 gate |
| `MOBILE_BOTTOM_NAV_ENABLED` | (b) | |
| `STICKY_ADD_TO_CART_ENABLED` | (b) | |
| `RECENTLY_VIEWED_ENABLED` | (b) | history is client-side |
| `DEMO_ACCOUNT_ENABLED` | (b) | demo credentials card on the sign-in page |
| `AGENT_COMMERCE_ENABLED`, `AGENT_HOSTED_PAYMENT_ENABLED`, `PRODUCT_FEEDS_ENABLED` | agent surface | folded into `TenantConfig` as `agentCommerceEnabled` and friends, enforced by the gateway; `chat_widget` builds on the first |
| `META_CAPI_ENABLED`, `MYDATA_ENABLED`, `ACS_DYNAMIC_PRICING_ENABLED`, `B2B_LOYALTY_ENABLED`, `LOYALTY_NEW_CUSTOMER_BONUS_ENABLED`, `LOYALTY_TIER_MULTIPLIER_ENABLED`, `OPS_ALERT_*_ENABLED` | (s) | never read by a client |

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
| `favourites` | | `FAVOURITES_ENABLED` | `product/views/favourite.py` |
| `newsletter` | | `NEWSLETTER_ENABLED` | `user/views/` subscription views (token links in sent emails stay open) |
| `feedback` | | `FEEDBACK_ENABLED` | `contact/views.py` |
| `loyalty` | `loyalty_enabled` | `LOYALTY_ENABLED` | `loyalty/views/loyalty.py` |
| `promotions` | `promotions_enabled` | `PROMOTIONS_ENABLED` | `promotion/views.py`, cart coupon actions |
| `gift_cards` | `gift_cards_enabled` | `GIFT_CARDS_ENABLED` | `giftcard/views.py` |
| `wholesale` | `b2b_enabled` | `B2B_WHOLESALE_ENABLED` | `b2b/views.py` |
| `product_suggestions` | `recommendations_enabled` | `PRODUCT_SUGGESTIONS_ENABLED` | `recommendation/views.py`, `search/views.py`; the order email, candidate rebuilds and weight learning through `recommendation.context.recommendations_active` |
| `product_alerts` | | `PRODUCT_ALERTS_ENABLED` | `product/views/alert.py` |
| `contact_attachments` | | `CONTACT_ATTACHMENTS_ENABLED` | `contact/views.py` (`contact-attachment`) |
| `chat_widget` | `agent_commerce_enabled` | `AGENT_COMMERCE_ENABLED`, `CHAT_WIDGET_ENABLED` | `/api/v1/agent/*` by the agent-commerce gates; the gateway's own `/chat` is enforced there |

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

## Not yet gated: `CART_ENABLED` and `CATALOGUE_ENABLED`

Both have real API surface (`/cart*`, `/order`, `/product*`, category and
brand listings, search) that nothing refuses today: the settings only hide
the storefront chrome and pages. The agent gateway calls those routes
(`/cart`, `/cart/item`, `/cart/reserve-stock`, `/order`, `/product`,
`/product/category/all`) and does not read a `cart` or `catalogue`
feature, so gating them at the API would turn a store's agent checkout and
catalogue feeds into 404s before the gateway knows to stop offering them.
They join the table after the gateway reads the feature, gateway first.

## Store languages

`Tenant.available_locales` is the list of languages a store's storefront
serves; empty means the default locale only. Platform staff set it on the
tenant form's Branding tab (`TenantAdmin.fieldsets`); a store operator sees
it read-only, because it is not in `TENANT_SELF_EDITABLE_FIELDS`. The field
accepts only `STOREFRONT_LOCALES` (the storefront's routes, narrower than
`settings.LANGUAGES`: `de` is a content language with no `/de` routes) and
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
