# App map

Every Django app in this repository: what it owns, where its entry points
are, and the doc to read before changing it. The code wins over this
page: when they differ, fix the page in the same change.
`tests/unit/test_doc_references.py` fails when an app under `src/` is not
named here, and when a path named here does not exist.

## Layout

- **Source lives in `src/`.** Every app, `src/settings.py` and the `asgi`
  package are in `src/`, and `src/` is on the Python path, so an import
  names the app and never `src` (`from order.services import ...`). A file
  is named by its path (`src/order/services.py`), a module by its dotted
  name (`order.services`). Apps are flat: one folder per app, no nesting.
- **Two app lists.** `SHARED_APPS` migrate into the `public` schema,
  `TENANT_APPS` into every store's own schema, and `INSTALLED_APPS` is
  their union (`src/settings.py`). The list an app is in decides where its
  tables live, and so which host can serve it. The headings below follow
  the lists. The cross-service view is
  `grooveshop-docs/src/dev/platform/multi-tenancy.md`.
- **A module becomes a package at about 500 lines.** `services.py` turns
  into `services/` with one module per subject, and every import site
  moves in the same change (no re-export shim). The modules that are far
  past that today (`src/order/services.py`, `src/shipping_acs/services.py`,
  `src/tenant/models.py` and others) are being split by the architecture
  programme, epic #558.
- **Tasks live in `tasks`, receivers in `signals`.** A Celery task is
  defined in the app's `tasks` module (or package), a signal receiver in
  its `signals` module (or package), and the receivers are connected by
  importing that module from the app's `AppConfig.ready()`. A task that
  changes module keeps its registered name (`name="<its old dotted path>"`).
- **Imports between apps are contracts.** `[tool.importlinter]` in
  `pyproject.toml` lists what may import what; run `uv run lint-imports`.

## Settings, URLs and ASGI

- **`src/settings.py`** is one file, and `DJANGO_SETTINGS_MODULE` is
  `settings`. `SYSTEM_ENV` (`dev`, `ci`, `staging`, `production`) switches
  the environment-specific behaviour. Everything else comes from `.env`
  through python-dotenv.
- **`src/core/urls.py`** is `ROOT_URLCONF` and mounts every app under
  `api/v1/`. It is built from named groups so two hosts can serve
  different subsets: `_root_shared_patterns`, `_shared_i18n_patterns`,
  `_storefront_i18n_patterns` and `_storefront_admin_i18n_patterns`.
  `public_shared_urlpatterns` is what the platform host serves, and
  `src/tenant/urls_public.py` (`PUBLIC_SCHEMA_URLCONF`) adds the platform
  admin to it. A store's catalogue API does not exist on the platform host.
- **`src/asgi/__init__.py`** builds the Channels `ProtocolTypeRouter`:
  HTTP goes to Django, WebSocket goes through
  `AllowedHostsOriginValidator`, `TenantWebsocketMiddleware` and
  `TokenAuthMiddlewareStack` to `ws/notifications/` and
  `NotificationConsumer`. The service is ASGI only: Gunicorn with Uvicorn
  workers in production, `uvicorn asgi:application --reload` locally.

## Shared apps (the `public` schema)

### `core/`

Shared infrastructure. Models: `Translation` (the database overlay of the
translation catalogue), `SweepCursor` and `CachePurgeLog`
(`src/core/cache/models.py`). It owns:

- the abstract models (`src/core/models.py`) and the custom fields
  (`src/core/fields/`);
- `BaseModelViewSet` (`src/core/api/views.py`), permissions
  (`src/core/api/permissions.py`), Knox token authentication
  (`src/core/api/tokens.py`), throttles (`src/core/api/throttling.py`) and
  the OpenAPI helpers (`src/core/utils/serializers.py`,
  `src/core/api/schema.py`);
- filters, pagination and managers (`src/core/filters/`,
  `src/core/pagination/`, `src/core/managers/`);
- the Celery app (`src/core/celery.py`), `MonitoredTask` and the system
  tasks (`src/core/tasks.py`), the retry sets (`src/core/retry.py`) and
  the retention helper (`src/core/retention.py`);
- the cache backend (`src/core/caches.py`) and the cache surface registry
  (`src/core/cache/`);
- middleware (`src/core/middleware/`), the email templates
  (`src/core/templates/`) and the root URL conf.

Read `CLAUDE.md` ("Core Base Classes and Patterns"), `docs/client-ip.md`,
`docs/error-contract.md`, `docs/image-uploads.md`, `docs/rich-text.md`,
`docs/safe-fetch.md`, `docs/celery-results.md` and `docs/migrations.md`
for the part you touch.

### `tenant/`

The multi-tenancy control plane. Models: `Tenant` (the django-tenants
`TenantMixin`, with branding, plan flags and every per-store credential
field), `TenantDomain`, `UserTenantMembership` (staff roles),
`PlatformStaffToken`, `TenantArchive`, `TenantStatsSnapshot` and
`DeferredDispatch`. A domain is a bare lowercase host name:
`tenant.validators.validate_store_hostname` runs in the form and in
`save()`, because routing matches the text exactly.

Entry points: `src/tenant/urls.py` (`/api/v1/tenant/resolve` and
memberships, on every host), `src/tenant/urls_public.py`,
`src/tenant/middleware.py`, `src/tenant/auth_backends.py`
(`PlatformStaffBackend`), `src/tenant/api_tokens.py` (`StaffBearer`
tokens), `src/tenant/membership.py` (`is_store_staff`, `bound_tenant`),
`src/tenant/role_scopes.py`, `src/tenant/credentials.py` (per-store
secrets, Fernet-encrypted at rest, no platform-wide fallback for money or
secrets), `src/tenant/provisioning.py`, `src/tenant/lifecycle.py`,
`src/tenant/billing.py`, `src/tenant/cache.py` (`make_tenant_key`,
`tenant_resolve_key`), `src/tenant/celery.py` (`TenantTask`,
`run_for_all_tenants`, `dispatch_on_commit`) and `src/tenant/tasks.py`
(every beat fan-out).

Read a per-store field through `tenant.membership.bound_tenant()`, never
`connection.tenant`: under `schema_context` that is a bare `FakeTenant`
with no fields, and `bound_tenant` returns the real row.

Read `docs/api-staff-identity.md`, `docs/secret-encryption.md`,
`docs/deferred-dispatch.md` and `docs/store-features.md`.

### `scheduler/`

The periodic schedule's state. `CELERY_BEAT_SCHEDULE` in
`src/settings.py` is the whole schedule; `PeriodicTask` holds only state
(enabled, last run, run count), and `scheduler.beat.CodeOwnedScheduler`
(`src/scheduler/beat.py`) reads the settings and deletes any row they do
not declare. Read `docs/scheduler.md`.

### `country/`, `region/`

`Country` and `Region`: reference rows shared by every store, seeded from
`src/country/seed_data.py` and `src/region/seed_data.py`.

### `devtools/`

No models. `CustomDjangoModelFactory` (`src/devtools/factories.py`), the
base class for the factories of translatable models; the demo store's
data (`src/devtools/demo_store.py` and the other `demo_*` modules); the
staging anonymiser (`src/devtools/anonymise/`); the seed commands. Read
`docs/testing.md` and `docs/staging-anonymise.md`.

## Apps in both lists

### `user/`

`UserAccount` (`AUTH_USER_MODEL`): the public schema holds platform staff
and each store's schema holds its customers. Also `UserAddress`,
`UserSubscription`, `SubscriptionTopic` and `UserDataExport`. Services are
a package (`src/user/services/`, with the data export and account
deletion in `src/user/services/gdpr.py`). Authentication is django-allauth
headless, mounted at `_allauth/` in `src/core/urls.py`; read
`docs/allauth-schema.md`.

### `emails/`

Every email the platform sends. An email is an `EmailType` declared by
its app (`src/order/emails.py`, `src/user/emails.py` and the other
`emails.py` modules) and sent only through `send_email`
(`src/emails/send.py`). Models: `EmailSwitch` (a store switches a type
off), `EmailOptOut`, `EmailLog`, `EmailEvent` and `EmailSuppression`.
Also the admin preview (`src/emails/preview.py`) and the Resend webhook
(`src/emails/webhooks.py`). Read `docs/emails.md`.

### `admin/`

No models. The Unfold admin: `MyAdminSite` (`src/admin/admin.py`, the
store admin), `PlatformAdminSite` (`src/admin/platform_site.py`, the
control plane), `BaseModelAdmin` (`src/admin/base.py`), the dashboards
(`src/admin/dashboard/`), the sidebar (`src/admin/navigation.py`) and the
reports (`src/admin/reports.py`, `src/admin/turnover.py`,
`src/admin/profit.py`). Read `docs/turnover-report.md` and
`docs/profit-report.md`; the cross-service view is
`grooveshop-docs/src/dev/django/admin.md`.

## Store apps (each store's schema)

### `product/`

The catalogue. Models under `src/product/models/`: `Product`,
`ProductCategory` (MPTT), `ProductImage`, `ProductCategoryImage`, `Brand`,
`Attribute`, `AttributeValue`, `ProductAttribute`, `ProductVariantGroup`,
`ProductReview`, `ProductFavourite`, `ProductAlert`, `ProductRelation`,
`ProductHighlight` and `ProductPriceSpell`. Views under
`src/product/views/`, one module per model. Pricing is a package
(`src/product/pricing/`); `src/product/tasks.py` holds the product alerts
and the price-record reconciliation. The stock quantity is a column on
`Product`, but stock is reserved and released in `src/order/stock.py`.
Read `docs/product-pricing.md` and `docs/api/products.md`.

### `order/`

Orders, payments, refunds, tax documents and stock. **Read
`docs/order/index.md` first**: it says which file of the order reference
covers what you are changing. Models under `src/order/models/`: `Order`,
`OrderItem`, `OrderHistory`, `OrderItemHistory`, `OrderAttribution`,
`OrderLegalAcceptance`, `OrderVatLine`, `PaymentTransaction`,
`PendingPaymentFee`, `RefundAttempt`, `RefundNotification`,
`ChargebackDecision`, `CreditAllocation`, `Invoice`, `InvoiceCounter`,
`RetailCredit`, `CreditCounter`, `StockReservation`, `StockLog`, `Expense`
and `VivaWebhookEvent`.

Entry points: `src/order/services.py` (creation, the state machine,
cancel, refund), `src/order/stock.py` (`StockManager`),
`src/order/payment.py` (the payment providers),
`src/order/payment_ledger.py` (`PaymentLedger`),
`src/order/signals/handlers.py` (the side effects of a change),
`src/order/tasks.py`, `src/order/emails.py`, `src/order/notifications.py`
(live toasts), `src/order/mydata/` and `src/order/views/`.

### `cart/`

`Cart`, `CartItem` and `CartAttribution` (`src/cart/models/`),
`src/cart/services.py`, the guest and customer cart API under
`src/cart/views/`, and the agent's cart token
(`src/cart/agent_token.py`, `docs/cart-agent-token.md`).

### `blog/`

`BlogPost`, `BlogCategory` (MPTT), `BlogAuthor`, `BlogComment` and
`BlogTag` under `src/blog/models/`, views per model under
`src/blog/views/`. Indexed in Meilisearch beside the products. Read
`docs/blog-related-posts.md`.

### `promotion/`, `giftcard/`, `b2b/`

- `promotion/`: `Promotion`, `PromotionCode`, `PromotionRedemption` and
  `CartPromotionCode`; the rules are evaluated in
  `src/promotion/services.py`.
- `giftcard/`: `GiftCard`, `GiftCardPurchase`, `GiftCardTransaction` (the
  ledger) and `GiftCardPause`; the rules are in
  `src/giftcard/services.py`.
- `b2b/`: `BusinessProfile`, `CustomerGroup` and `PriceListItem`; VIES
  validation in `src/b2b/vies.py`, the rest in `src/b2b/services.py`.

Each is behind a store feature (`docs/store-features.md`). Cross-service
views: `grooveshop-docs/src/dev/flows/promotions-and-gift-cards.md` and
`grooveshop-docs/src/dev/flows/b2b.md`.

### `loyalty/`

`LoyaltyTier` (translatable), `PointsTransaction` and `LoyaltyPause`.
`src/loyalty/services.py` awards and reverses points,
`src/loyalty/signals.py` reacts to order events and
`src/loyalty/tasks.py` expires points. Cross-service view:
`grooveshop-docs/src/dev/flows/loyalty.md`.

### `shipping/`, `shipping_acs/`, `shipping_boxnow/`

- `shipping/`: the carrier abstraction (`src/shipping/interfaces.py`),
  `ShippingProvider`, the per-store `ShippingRate` table
  (`src/shipping/models/rate.py`: the only source of a shipping price,
  availability and weight cap), `ShippingService`
  (`src/shipping/services.py`) and the operational alerts
  (`src/shipping/alerts.py`). Read `docs/order/shipping.md`.
- `shipping_acs/`: `AcsShipment`, `AcsPickupList`, `AcsPickupListRun`,
  `AcsCodPayout`, `AcsStation`, `AcsTrackingEvent`, `AcsApiCall`,
  `AcsVoucherHold` and `AcsUnrecognisedVoucher`; the HTTP client
  (`src/shipping_acs/client.py`), `AcsService`
  (`src/shipping_acs/services.py`) and the tasks. Read
  `docs/order/shipping-acs.md` and `docs/acs-debugging.md`.
- `shipping_boxnow/`: `BoxNowShipment`, `BoxNowLocker` and
  `BoxNowParcelEvent`; the client, `BoxNowService`, the tasks and the
  webhook (`src/shipping_boxnow/views/webhook.py`). Read
  `docs/order/shipping-boxnow.md`.

The import contracts keep the two carrier apps from importing each other
("shipping_acs does not import shipping_boxnow" and its mirror in
`pyproject.toml`), and "a carrier app is reached only through shipping"
lists the imports that still break that rule.

### `pay_way/`, `vat/`, `tag/`, `contact/`

`PayWay` (a store's payment methods, with `PayWayShippingExclusion`;
`src/pay_way/services.py`; read `docs/order/payments.md`), `Vat`,
`Tag` and `TaggedItem` (generic tagging through `TaggedModel`), and
`contact/` (`Contact`, `ContactAttachment`, `Feedback`, with the
attachment scan and the retention sweeps in `src/contact/tasks.py`).

### `page_config/`

Translatable `ContentPage` (policies, FAQ, information pages) and the
legal documents: the platform's texts and their rollout
(`src/page_config/legal_documents.py`, `src/page_config/legal_guard.py`)
and the version each order accepted (`LegalDocumentVersion`). Pages and
menus are the page builder's.

### `texts/`

Store-edited storefront wording: `TextOverride`, the store admin's text
editor (`src/texts/editor.py`) and the public read of the overrides. The
catalogue and the syntax check are the storefront's. Read
`docs/store-texts.md`.

### `builder/`

The storefront page builder: `BuilderDocument` (theme, section groups,
templates, pages, each with a draft), immutable `BuilderVersion` and
`BuilderRelease`, `BuilderScheduledPublish`, `BuilderPreviewLink`, the
derived `BuilderReference` index and the media library (`MediaAsset`).
It serves the staff API and the public read of the current release
(`src/builder/views.py`); the writes go through
`src/builder/services.py`. The block catalogue is the storefront's. Read
`docs/builder.md`.

### `recommendation/`

`RecommendationSlot`, `RecommendationCandidate` and `RecommendationEvent`;
the engine (`src/recommendation/engine.py`) and its strategies
(`src/recommendation/strategies/`). Read `docs/recommendations-engine.md`.

### `notification/`

`Notification` and `NotificationUser`, the Channels consumer
(`src/notification/consumers.py`), the per-user group names
(`src/notification/groups.py`) and the single-use WebSocket ticket
endpoint (`src/notification/views/websocket.py`).

### `agent/`

No models. The OAuth-scoped `/api/v1/agent/*` surface that the agent
gateway calls: the shopper tools (`src/agent/views.py`, behind the
gateway's `/mcp`, with the store's own issuer and customers) and the
read-only store-admin tools (`src/agent/staff_views.py`, `/agent/staff/*`
behind `/mcp/admin`). Django is the OAuth 2.1
authorization server through `allauth.idp`. Staff tokens come from the
platform's staff issuer on `STAFF_CONNECT_HOST` (`src/agent/staff.py`,
`src/agent/staff_connect.py`, `agent.urls_connect`): `allauth.idp.oidc`
is in `SHARED_APPS` too, so staff tokens live in `public` and customers'
tokens in each store's schema. Cross-service views:
`grooveshop-docs/src/dev/flows/agentic-commerce.md` and
`grooveshop-docs/src/dev/flows/staff-mcp.md`.

### `search/`, `meili/`

`meili/` is the Meilisearch layer: `IndexMixin` with a `MeiliMeta` inner
class (`src/meili/models.py`), the client (`src/meili/_client.py`), the
index and delete tasks (`src/meili/tasks.py`) and the management
commands. `search/` is the product-facing side: the product and blog
search views (`src/search/views.py`), Greeklish transliteration
(`src/search/transliteration.py`) and the `SearchQuery` / `SearchClick`
analytics. Read `docs/api/search.md` and the files under `docs/search/`.

### `meta_capi/`

Meta Conversions API: `MetaCapiEventLog`, the receivers on order and
registration events (`src/meta_capi/signals.py`) and the tasks that post
the events (`src/meta_capi/tasks.py`).

### `indexnow/`

IndexNow notifications to search engines: the receivers
(`src/indexnow/signals.py`) queue a published product or blog post that
changed as a `PendingUrl` row, and `submit_pending_urls`
(`src/indexnow/tasks.py`) sends one POST per store. Read
`docs/indexnow.md`.

## Payments have no app

Stripe is dj-stripe (`djstripe` in `TENANT_APPS`, its webhook under
`stripe/` in `src/core/urls.py`). Viva Wallet is
`src/order/views/viva_webhook.py` and `src/order/payment_events.py`. Both
read their credentials from the `Tenant` row through
`src/tenant/credentials.py`. Read `docs/order/payments.md`.
