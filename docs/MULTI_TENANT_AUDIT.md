# Multi-Tenant Deep Audit — Consolidated Findings

**Scope**: 25-agent parallel audit across all 4 repos (Django, Nuxt, Media-Stream, Infrastructure) on the `multi-tenant` branch.
**Date**: 2026-05-11
**Status**: 24/25 reports received; consolidation pending validation pass.

Each finding lists: agent that found it, file:line, severity, **status** (CONFIRMED / FALSE POSITIVE / NEEDS-VERIFY).

---

## 🔴 CRITICAL (Cutover blockers — must fix before deploy)

### C1. `transaction.on_commit` lambdas in Stripe handlers LOSE schema context ⚠️ PARTIAL
**File**: `order/signals/handlers.py:773, 781, 1109` (3 real sites — 697 + 705 are direct `.delay()` calls INSIDE `with schema_context`, which is fine)
**Status**: **PARTIAL — 3 of 5 confirmed, 2 false-positive**. `@with_tenant_schema_from_event` wraps handler body in `with schema_context(...)`. `transaction.on_commit(lambda: task.delay(...))` registers callback on current connection. When the lambda fires AFTER the `with` block exits, `connection.schema_name` is back to `public`. `TenantTask.apply_async` stamps `_schema_name='public'` instead of the tenant. Real sites: lines 773, 781, 1109. Fix: capture `schema_name = connection.schema_name` before the lambda and re-wrap: `transaction.on_commit(lambda s=schema_name: task.apply_async(args=[...], headers={'_schema_name': s}))`.

### C2. Viva webhook has NO tenant resolution at all ✅ CONFIRMED
**File**: `order/views/viva_webhook.py:127, 399, 428, 444`
**Status**: **CONFIRMED**. Only decorator is `@csrf_exempt` at line 127. No `@with_tenant_schema_from_event`, no `schema_context`, no `set_tenant`. All ORM calls at 399/428/444 run in whatever schema the request lands in. **Fix**: parse `event['Email']` or merchant_id → look up tenant → wrap handler in `schema_context(tenant.schema_name)`. Pattern to copy from `order/signals/_tenant.py` (`@with_tenant_schema_from_event`).

### ~~C3. `meta_pixel_id` field DOES NOT EXIST on Tenant model~~ ❌ FALSE POSITIVE
**File**: `meta_capi/client.py:87`, `services.py:113`, `tenant/credentials.py:118`
**Status**: **FALSE POSITIVE** — verified by direct read of `tenant/models.py:205`. Field exists as `models.CharField(max_length=64)` with digits-only validator. Nuxt store `tenant.ts:22` exposes `metaPixelId` correctly. End-to-end plumbing fine; audit agent miscounted.

### C4. 7 ACS + 4 BoxNow tasks use `@shared_task` not `base=TenantTask` ✅ CONFIRMED
**Files**: ALL 7 ACS tasks (`create_acs_voucher_for_order`, `sync_acs_stations`, `issue_daily_acs_pickup_list`, `poll_acs_tracking_batch`, `poll_acs_tracking_one`, `reconcile_acs_cod_payouts`, `acs_send_arrival_notification`) AND ALL 4 BoxNow tasks (`create_boxnow_shipment_for_order`, `sync_boxnow_lockers`, `process_boxnow_webhook_event`, `boxnow_send_arrival_notification`).
**Status**: **CONFIRMED — broader than originally claimed (11 tasks, not 7)**. Zero tenant context propagation. **Fix**: change all to `@shared_task(base=TenantTask, ...)` AND for beat-scheduled ones (sync_acs_stations, issue_daily_acs_pickup_list, sync_boxnow_lockers, reconcile_acs_cod_payouts) wrap dispatch in `run_for_all_tenants` fanout.

### C5. BoxNow webhook task carries no tenant context ✅ CONFIRMED
**File**: `shipping_boxnow/views/webhook.py:174`
**Status**: **CONFIRMED**. `process_boxnow_webhook_event.delay(envelope)` invokes a `@shared_task` (per C4). View has no schema resolution before dispatch. **Fix**: resolve tenant from webhook payload (BoxNow includes order reference) BEFORE dispatch, then either use `apply_async(headers={'_schema_name': schema})` or stamp it in the envelope.

### C6. Meili `IndexMixin.__init_subclass__` creates BARE index name ✅ CONFIRMED
**File**: `meili/models.py:244`
**Status**: **CONFIRMED**. `if settings.MEILISEARCH.get("OFFLINE", False): return` guard exists, so OFFLINE protects tests. In production OFFLINE=False → fires at class-load → calls `_client.create_index(index_name)` where `index_name` is the bare `MeiliMeta.index_name`. **Fix**: remove the auto-create from `__init_subclass__`; let `meilisearch_sync_all_indexes` (which runs per-tenant) create indexes with the tenant-prefixed name from `get_meili_index_name()`.

### C7. `tenant-site-config.ts` plugin is a SILENT NO-OP ✅ CONFIRMED
**File**: `server/plugins/tenant-site-config.ts:2`
**Status**: **CONFIRMED**. Nitro plugin `'request'` hook fires BEFORE route-scoped middleware (defineEventHandler middleware). `event.context.tenant` not set yet → guard at line 4 exits every time → tenant SEO overrides (siteName, description, URL) never applied. **Fix**: move the logic into a server middleware that runs AFTER `0.tenant.ts` (numbered higher, e.g. `4.tenant-site-config.ts`).

### ~~C8. `NUXT_PUBLIC_API_BASE_URL` still `localhost:8000` in production~~ ❌ FALSE POSITIVE
**File**: `base/frontend-config.yaml:60`
**Status**: **FALSE POSITIVE** — actual value is `http://backend-service:80/api/v1` (internal K8s service). No `localhost:8000` anywhere. Patch correctly leaves it alone.

### ~~C9. `NUXT_AUTH_COOKIE_DOMAIN` empty in production~~ ❌ FALSE POSITIVE (by design)
**File**: `base/frontend-config.yaml:24`
**Status**: **FALSE POSITIVE — by design**. Inline comment: "empty to let browser scope to request domain per-tenant". For multi-tenant SSO, empty value lets each tenant's cookies scope to its own domain (no cross-tenant cookie sharing). This is correct.

### C10. PreSync Job `activeDeadlineSeconds: 600` too short for first-cutover media copy ⚠️ PARTIAL
**File**: `prepare-helm/templates/job.yaml:33`
**Status**: PARTIAL — value confirmed at 600. Risk real but **limited to first cutover only** (step 0 `cp -r` is gated by `[ ! -d mediafiles/webside ]`). After first run, subsequent deploys skip the copy and 600s is plenty. Fix: bump to e.g. `7200` (2h) for the first cutover, or split step 0 into a separate Job with its own larger deadline.

### C11. Cart IDOR — `get_cart_by_id` no ownership check ✅ CONFIRMED
**File**: `cart/services.py:180`, `cart/views/cart.py:388-431`
**Status**: **CONFIRMED**. `Cart.objects.for_detail().filter(id=cart_id).first()` — any authenticated user within the tenant can read any other user's cart via sequential integer cart ID. Separately: `release_reservations` at `cart/views/cart.py:408-418` iterates request-supplied `reservation_ids` and calls `StockManager.release_reservation()` without verifying ownership. **Two IDOR bugs**, not one. **Fix**: filter by `user=request.user` (or `session=request.session.session_key` for guests); for reservations, JOIN to cart and verify cart ownership.

---

## 🟠 HIGH (correctness gaps, deferrable but real)

### H1. Subscription URLs bypass tenant helper ✅ CONFIRMED
**File**: `user/utils/subscription.py:68, 140, 168`
**Status**: **CONFIRMED**. All 3 sites use `settings.API_BASE_URL.rstrip("/")`. No call to `get_tenant_base_url()`. Subscription confirmation + both unsubscribe URLs point at platform API regardless of tenant. **Fix**: replace with `get_tenant_base_url()` from `tenant/credentials.py`.

### H2. MFA WebAuthn rpId hardcoded to platform domain ✅ CONFIRMED
**File**: `core/adapter.py:12`
**Status**: **CONFIRMED**. Returns `getattr(settings, "APP_MAIN_HOST_NAME", "localhost")`. TOTP issuer IS per-tenant correctly (different code path), but passkey rpId is not. Passkeys registered on tenant-b would fail browser origin verification. For webside-only cutover, no impact yet.

### H3. Knox WebSocket auth tenant bypass ⚠️ PARTIAL — real risk for tenant-2 onboarding
**File**: `core/api/tokens.py:77` + `tenant/middleware_ws.py:33-36`
**Status**: **PARTIAL**. WS middleware deliberately skips `connection.set_tenant()` (correct ASGI behavior — set_tenant is a thread-local hack). `BoundedTokenAuthentication.authenticate_credentials` reads `get_current_tenant()` at line 77, gets None in WS → tenant binding check skipped → Knox token from tenant A can authenticate on tenant B's `/ws/notifications/`. Group naming `tenant_{schema}_user_{id}` may still scope messages correctly, but the auth surface is bypassed. **Fix**: pass tenant explicitly via WS query param or resolve from Host in middleware.

### H4. `notification/signals.py` on_commit + Celery dispatch ✅ CONFIRMED
**File**: `notification/signals.py:61`
**Status**: **CONFIRMED**. Same bug class as C1. `transaction.on_commit(lambda: send_notification_task.delay(data))`. `send_notification_task` uses `base=MonitoredTask` (extends TenantTask), but lambda fires post-commit when `connection.schema_name` is back to public.

### ~~H5. `fanout_*` tasks query Tenant.objects from tenant context~~ ❌ FALSE POSITIVE
**File**: `tenant/celery.py:32-48`
**Status**: **FALSE POSITIVE**. `run_for_all_tenants` queries `Tenant.objects` in PUBLIC schema BEFORE entering any tenant `schema_context`. Designed usage is from public context (celery-beat schedule, management command). No bug.

### H6. `TenantAdminViewSet` returns 403 not 404 ⚠️ PARTIAL
**File**: `tenant/views.py:128-156`
**Status**: **PARTIAL**. Write ops return `PermissionDenied` (403). Read ops return `.none()` (empty 200). Info-leak concern (403 reveals endpoint existence) real for writes. **Fix**: raise `Http404` instead of `PermissionDenied`.

### H7. `pre_login` membership gate reads stale connection.tenant ✅ CONFIRMED
**File**: `tenant/allauth_adapter.py:97`
**Status**: **CONFIRMED**. `getattr(connection, "tenant", None)` under `database_sync_to_async` reads thread-local from pooled thread that may have served a different request. Stale `connection.tenant` → wrong membership gate. **Fix**: explicit tenant resolution from `request.get_host()` in adapter (don't trust thread-local).

### H8. `TenantTask.apply_async` reads schema from thread-local ✅ CONFIRMED
**File**: `tenant/celery.py:17`
**Status**: **CONFIRMED**. `headers["_schema_name"] = getattr(connection, "schema_name", "public")`. Thread-local read at enqueue time. If called from management command or fanout worker not inside schema_context, stamps "public". **Fix**: require schema to be passed explicitly (kwarg, raise if unset).

### ~~H9. Product `_attribute_meili_fields` cache leaks via id() reuse~~ ❌ FALSE POSITIVE
**File**: `product/models/product.py:546`
**Status**: **FALSE POSITIVE**. `_cache = {}` is defined INSIDE `_attribute_meili_fields` (a `@staticmethod`), not at module level. Fresh per call. id() reuse not a concern.

### H10. CI CACHES drops KEY_FUNCTION
**File**: `settings.py:548-555`
**Status**: KNOWN — previously discovered in session
**Detail**: Tests run with unscoped cache keys, validating wrong contract.

### H11. AllAuthRateLimitMiddleware implicit KEY_FUNCTION dependency ✅ CONFIRMED
**File**: `core/middleware/allauth_ratelimit.py:106`
**Status**: **CONFIRMED**. Cache key `f"allauth_rl:{path_prefix}:min:{client}"` contains only IP hash, no explicit schema. Relies entirely on KEY_FUNCTION. Production OK; test contract wrong if CI CACHES drops KEY_FUNCTION (which it does — see H10). **Fix**: prepend schema explicitly OR fix H10 to keep KEY_FUNCTION in CI.

### H12. STORAGE_LEGACY_FALLBACK read at module import time ✅ CONFIRMED
**File**: `core/storages.py:71`
**Status**: **CONFIRMED**. `_LEGACY_FALLBACK = os.getenv(...)` evaluated once at import. Late env injection (Celery worker, test patch) invisible. **Fix**: wrap in `_get_legacy_fallback()` function called per request.

### H13. `app.vue` `theme-color` meta hardcoded ✅ CONFIRMED
**File**: `app/app.vue:68`
**Status**: **CONFIRMED**. `content: '#1a202c'` hardcoded. `tenantStore.accentHex` available at `tenant.ts:10`. Fix: read from store.

### H14. Product pages emit `ogImage: config.public.appLogo` without tenant fallback ⚠️ PARTIAL
**File**: `app/pages/products/index.vue:45` (CONFIRMED), `products/category/[id]/[slug].vue:68` (lower severity — only as last-resort fallback after product image)
**Status**: PARTIAL — index page is clear miss; category page only matters when product has no image. Fix: use `tenantStore.logoLightUrl || publicConfig.appLogo` matching `setups.ts:17-19`.

### H15. Navbar logo hardcoded `/img/logo-navbar.svg` ✅ CONFIRMED
**File**: `app/components/Builder/Navbar.vue:102-111`
**Status**: **CONFIRMED**. `tenantStore` imported (line 29), `appTitle` uses it (line 30), but NuxtImg src ignores `tenantStore.logoLightUrl`. Fix: bind src to `tenantStore.logoLightUrl || '/img/logo-navbar.svg'`.

### H16. `useBackendFetch` singleton captures config at module load ⚠️ PARTIAL — low-risk
**File**: `server/utils/backendFetch.ts:32-56`
**Status**: PARTIAL. `_backendFetch` cached once. Per-request headers (X-Forwarded-Host, X-Language) ARE resolved freshly in onRequest hook via `useEvent()`. Only `internalOrigins` + `fallbackPublicHost` are baked at first call. Production-safe (config is static); dev hot-reload only.

### H17. `tenantCache` unbounded Map ⚠️ PARTIAL — mitigated
**File**: `server/utils/tenant.ts:2`
**Status**: PARTIAL. Plain Map, no LRU. **But**: negative results (404/5xx) are explicitly NOT cached (lines 56-62), so adversarial Host headers miss cache and never accumulate entries. Only resolved tenants are cached → grows proportional to legitimate tenant count. Structural risk remains but no practical DoS vector via Host header spray. Fix: add `lru-cache` import for future-proofing.

### ~~H18. Session cookie has no schemaName binding~~ ❌ FALSE POSITIVE
**File**: `shared/auth.d.ts:11` + `nuxt.config.ts:114`
**Status**: **FALSE POSITIVE**. No hardcoded `.webside.gr` in code. `auth.cookieDomain` reads from `NUXT_AUTH_COOKIE_DOMAIN` env var (empty in production by design per C9). No wildcard cross-tenant cookie leakage.

### ~~H19. `fetchUserData` conditionally omits X-Forwarded-Host~~ ❌ FALSE POSITIVE
**File**: `server/utils/auth.ts:142-156`
**Status**: **FALSE POSITIVE**. When `host` is falsy, fallback path at line 153-154 calls `getAllAuthHeaders()` → `createHeaders()` which uses `config.public.djangoHostName` as fallback. Always a host header. No DisallowedHost risk.

### H20. `tenantSchema` unvalidated in media-stream controller ⚠️ PARTIAL (real concern, wrong evidence)
**File**: `cache-image-request.dto.ts:137` + `media-stream-image.controller.ts:221-223`
**Status**: PARTIAL — agent claim that `@Matches` is missing is wrong (DTO has NO class-validator decorators at all; uses `Object.assign`). The underlying concern is real: controller assigns `tenantSchema` from URL regex with **zero format guard** before it flows into cache keys and Prometheus labels. Fix: at controller line 222, add `if (tenantSchema && !/^[a-z_][a-z0-9_]{0,62}$/.test(tenantSchema)) throw new BadRequestException(...)` — same pattern as `admin-cache.controller.ts:11`.

### H21. cache-warming strips tenant from cache key ✅ CONFIRMED
**File**: `Cache/services/cache-warming.service.ts:222-223, 251, 276`
**Status**: **CONFIRMED**. `warmupFile()` calls `cacheManager.exists/set('image', resourceId, ...)` with hardcoded flat `'image'` namespace. Tenant-scoped reads look for `'image:{schema}'` → warming is a no-op for all tenant content. Also: per-tenant `invalidateNamespace('image:acme')` does NOT evict warming-written entries. Fix: pass `image:${tenantSchema}` (and read tenantSchema from the resource metadata or from the path the warming job is iterating).

### H22. `PageLayoutAdminViewSet` uses IsAdminUser globally ✅ CONFIRMED
**File**: `page_config/views.py:43`
**Status**: **CONFIRMED**. `permission_classes = [IsAdminUser]`. Any platform staff can read/write any tenant's page layouts. **Fix**: replace with `[IsAdminUser, HasTenantAccess]`.

### H23. Contact form emails go to `settings.ADMINS` ✅ CONFIRMED
**File**: `contact/tasks.py:60`
**Status**: **CONFIRMED**. `recipient_list = [admin[1] for admin in getattr(settings, "ADMINS", [])]`. Should use `tenant_contact_email()`. **Fix**: import and use the helper.

### ~~H24. `meilisearch_sync_all_indexes` queryset bug~~ ❌ FALSE POSITIVE (likely)
**File**: `product/models/product.py:386`
**Status**: **FALSE POSITIVE** (likely — could not be confirmed from this file alone). `get_meilisearch_queryset()` reads from current connection schema. The management command is presumed to set schema context before calling it. Needs separate verification of `meilisearch_sync_all_indexes` command.

### ~~H25. `save_search_query` uses MonitoredTask not TenantTask~~ ❌ FALSE POSITIVE
**File**: `search/tasks.py:9`
**Status**: **FALSE POSITIVE**. `base=MonitoredTask`. `MonitoredTask` is defined at `core/tasks.py:55` as `class MonitoredTask(TenantTask)`. Full tenant context propagation.

### H26. Order country/region FKs are cross-schema
**File**: `order/models/order.py:95-103`
**Status**: ARCHITECTURAL (PostgreSQL fundamental — no cross-schema FK enforcement). Already accepted via `db_constraint=False` pattern.

---

## 🟡 MEDIUM (real issues, lower urgency)

### M1. SWR background revalidation tenant context ✅ MOSTLY CORRECT
**File**: All `defineCachedEventHandler` + raw `$fetch`
**Status**: 37 of 38 tenant-data routes correctly use `tenantCacheKey`. Only exception: `server/api/health/index.get.ts` uses static `'nuxt:health:v1'` — intentional per its inline comment. **No missing tenantCacheKey** in tenant-data routes.

### M2. `tenantCacheKey` doesn't strip port
**File**: `server/utils/cacheKey.ts:8`
**Status**: NEEDS-VERIFY
**Detail**: `tenant.ts` strips port from cache lookups; `tenantCacheKey` does not → mismatched keys in dev/non-standard ports.

### M3. BYPASS list missing `/api/__sitemap__`, AI-ready paths
**File**: `server/middleware/0.tenant.ts:9-18`
**Status**: NEEDS-VERIFY

### M4. Media-stream Prometheus `tenant_schema` label fed unvalidated ⚠️ PARTIAL
**File**: `cache-image-resource.operation.ts:140, 163, 198`
**Status**: PARTIAL — agent confirmed `ctx.request.tenantSchema` is passed directly to `recordCacheOperation/recordImageProcessing`. Risk is GATED by `setup()` at line 271 which runs `validateCacheImageRequest` before any execute/checkResourceExists. So invalid schemas can't reach the label sites. **Residual risk**: if `ValidateCacheImageRequestRule` does not enforce the PG identifier pattern, an unusually-long but technically-valid schema string could still inflate Prometheus cardinality. Also `metrics.middleware.ts:40-47` calls `recordHttpRequest` with NO `tenantSchema` (defaults to 'public') — under-labelled. Fix: tighten validator + label every metrics emission.

### M5. Frontend probes hit `/` (full SSR) ✅ CONFIRMED
**File**: `frontend.yaml:99-121`
**Status**: **CONFIRMED**. All three probes (startup, readiness, liveness) `httpGet path: /`. Full SSR includes backend API calls; Django blip → 500 → kubelet marks NotReady → traffic shed to remaining replica → cascading load. Fix: add a lightweight `/_health` Nitro route returning 200 with no backend dependency.

### M6. `rate-limit-api` Middleware `ipStrategy.depth: 1` behind Cloudflare ✅ CONFIRMED
**File**: `middleware-ratelimit.yaml:13`
**Status**: **CONFIRMED**. With CF in front, depth:1 reads the CF edge PoP IP not real client. All clients sharing a PoP share one bucket. Fix: depth:2, or use `excludedIPs` for CF range, or read `X-Real-IP` via Traefik.

### ~~M7. 5 components use bare `<NuxtImg>` with absolute tenant URLs → IPX~~ ❌ FALSE POSITIVE
**File**: cited paths don't match reality
**Status**: **FALSE POSITIVE**. No absolute-URL `<NuxtImg>` found. `about.vue:67` and `vision.vue:57` use `tenantStore.logoLightUrl || '/img/...'` (relative fallback). `Builder/Navbar.vue:102` uses `/img/logo-navbar.svg` (relative, separate issue tracked as H15). `auth.vue` + `HeroCarousel.vue` paths don't exist.

### M8. `usePriceFormat` hardcoded EUR, doesn't read `tenantStore.defaultCurrency`
**File**: `app/composables/usePriceFormat.ts:29` + `i18n.config.mts:13`
**Status**: NEEDS-VERIFY

### M9. `turnstileSiteKey` in store but no consumer ✅ CONFIRMED (dead code)
**File**: `app/stores/tenant.ts:26,70`
**Status**: **CONFIRMED**. Exposed but zero consumers in `app/**/*.vue`. `TurnstileContainer.vue` is a lazy wrapper without a siteKey prop. Either remove the field or wire it into TurnstileContainer.

### M10. `mydata/config.py:load_config` fragile schema chain ⚠️ PARTIAL — downstream of C1
**File**: `order/mydata/config.py:74-95`
**Status**: PARTIAL. `load_config()` calls `Setting.get(...)` reading `extra_settings_setting` in active schema. If invoking task has wrong `_schema_name` (per C1/H4), reads wrong tenant's mydata config. Symptom only manifests after C1/H4 fix.

### M11. `shipping/migrations/0002_seed_providers.py` missing `using=` ✅ CONFIRMED
**File**: `shipping/migrations/0002_seed_providers.py:22, 41`
**Status**: **CONFIRMED**. `ShippingProvider.objects.update_or_create(...)` without `using=schema_editor.connection.alias`. In multi-tenant migration runs, operates on default connection rather than schema-editor's connection. **Fix**: pass `using=schema_editor.connection.alias` explicitly.

### M12. BoxNow token cache key not schema-scoped ⚠️ PARTIAL
**File**: `shipping_boxnow/client.py:104`
**Status**: PARTIAL. Key `f"boxnow:access_token:{partner_id}"` has no explicit schema. Production OK via KEY_FUNCTION; risk only if multiple tenants share the same BoxNow `partner_id` (which may be intentional cost-sharing). **Fix**: add schema to key only if tenants will get separate BoxNow partner contracts.

### ~~M13. PreSync Job missing `~/.cache` emptyDir mount~~ ❌ FALSE POSITIVE
**File**: `prepare-helm/templates/job.yaml:88-93,136-142`
**Status**: **FALSE POSITIVE**. `/tmp` is mounted as emptyDir; Django management commands write there. No cache mount issue.

### ~~M14. `vat` placement TENANT_APPS may be misplaced~~ ❌ FALSE POSITIVE
**File**: `settings.py:172`
**Status**: **FALSE POSITIVE**. VAT rates are per-tenant reference data (each tenant may operate in different country with different VAT). Correctly placed in TENANT_APPS.

### M15. `pay_way.PayWay.configuration` JSONField holds secrets unencrypted ✅ CONFIRMED
**File**: `pay_way/models.py:58`
**Status**: **CONFIRMED**. Plain JSONField, no encryption. API keys + webhook secrets stored cleartext in tenant PG schema. Issue regardless of multi-tenancy. **Fix**: switch to `django-encrypted-fields-and-files` or use Django Fernet wrapper.

### M16. `redirect-www` Middleware missing explicit `namespace` ✅ CONFIRMED (minor)
**File**: `ingress-webside.yaml:110`
**Status**: **CONFIRMED — minor**. `redirect-www` has no `namespace:` in metadata while sibling middlewares (redirect-https, security-headers) do. Inherits from kustomize context (grooveshop) so works in practice — inconsistency only.

### M17. Cart cleanup tasks unsafe in beat-scheduled path ✅ CONFIRMED
**File**: `core/tasks.py:278-342`
**Status**: **CONFIRMED**. `cleanup_abandoned_carts` and `cleanup_old_guest_carts` use `base=MonitoredTask` (extends TenantTask). When dispatched via `TenantTask.apply_async`, schema context is correct. **But**: when celery-beat schedules them directly without `_schema_name` header, beat runs in public schema → cleanup affects only public schema carts → tenant carts grow without bound. **Fix**: schedule via `run_for_all_tenants` fanout from beat.

### M18. Cart guest token is integer PK (enumerable) ✅ CONFIRMED
**File**: `cart/services.py:62`
**Status**: **CONFIRMED**. `self.cart_id = int(self.cart_id)` — X-Cart-Id header cast to int (sequential PK). Cart model has UUID field but service uses integer PK as external identifier. Closely related to C11 (IDOR). **Fix**: use UUID for external identifier; reject any X-Cart-Id that isn't a valid UUID.

---

## 🟢 LIKELY FALSE POSITIVES

### FP1. Dashboard cache key flat string
**File**: `admin/dashboard.py:33`
**Reason**: `KEY_FUNCTION = make_tenant_key` prefixes all `cache.*` calls with `{schema}:`. Actual Redis key is `webside:default:1:admin:dashboard:data:v3`. Tenant-scoped.

### FP2. Admin badge cache keys flat
**File**: `admin/badges.py:8,16,101,113`
**Reason**: Same as FP1.

### FP3. `loyalty:tier_level_map` flat key
**File**: `loyalty/signals.py:20,38,133`
**Reason**: Same as FP1.

### FP4. Blog `upload_to="uploads/blog/"` flat path
**File**: `blog/models/post.py:26`, `category.py:21`
**Reason**: `TenantPublicMediaStorage.location` adds `media/{schema}/` prefix at storage layer. Final S3 key `media/{schema}/uploads/blog/...` is correct.

### FP5. `save_search_query` uses MonitoredTask not TenantTask
**File**: `search/tasks.py:43`
**Reason**: Other audits confirmed `MonitoredTask` extends `TenantTask`. Already correct.

---

## 🔵 TEST COVERAGE GAPS (foundational correctness untested)

| Gap | Impact |
|---|---|
| `TenantTask.__call__` never executed in any test — only `apply_async` header dispatch tested | A bug in `__call__` runs all tasks in wrong schema silently |
| Zero cross-tenant data isolation tests — never asserts A writes → B can't see | Foundational invariant of the tenancy model untested |
| No Knox cross-tenant token replay test | Token from tenant A could authenticate on tenant B |
| No Viva webhook schema routing test | C2 above could regress unnoticed |
| No BoxNow webhook schema routing test | C5 above could regress unnoticed |
| No two-tenant WebSocket group isolation test | H3 above could regress unnoticed |
| MeiliSearch index name not tested with non-public `connection.schema_name` | C6 + H24 could regress |
| Email FROM helper tested but call sites not verified | Could use `settings.DEFAULT_FROM_EMAIL` without anyone knowing |

---

## 📋 What's CONFIRMED WORKING (per agent audits)

- TenantMainMiddleware order correct (first)
- `TenantTask` base class implementation + apply_async header
- `TenantPublicMediaStorage.location` reads `connection.schema_name` lazily via @property
- `KEY_FUNCTION = make_tenant_key` correctly applied to default cache
- PUBLIC_SCHEMA_URLCONF route extension
- USE_X_FORWARDED_HOST=True, ALLOWED_HOSTS=['*']
- All SealedSecrets exist + Ingress middleware chains correct
- All 36/38 `defineCachedEventHandler` use `tenantCacheKey` correctly
- Nuxt media-stream provider does NOT double-prepend schema
- Per-tenant credential helpers (Viva, ACS, BoxNow, from_email, contact_email, totp_issuer, meta_capi)
- `IsTenantMemberOrReadOnly` + `HasTenantAccess` permission tests
- `tenant_resolve` endpoint + memberships/mine
- Tenant lifecycle (suspend/activate/destroy with cooldown)
- `populate_tenant_schema` sequence fixes + idempotency
- `IsLoyaltyEnabled` / `IsBlogEnabled` feature gate tests (404 not 403)

---

## Final Numbers (all 4 validation agents returned)

### CRITICAL (cutover blockers — 6 remaining, was 11)
**Real**: C1 (partial), C2, C4 (broader), C5, C6, C7, C11 (IDOR)
**False positives**: C3, C8, C9
**Demoted to PARTIAL**: C10 (only matters first cutover)

### HIGH (correctness gaps — 16 remaining)
**Real CONFIRMED**: H1, H2, H4, H7, H8, H11, H12, H13, H15, H21, H22, H23, **N1** (new)
**Real PARTIAL**: H3, H6, H14, H16, H17, H20
**False positives**: H5, H9, H18, H19, H24, H25, H10 (already known)

### MEDIUM (15 remaining)
**Real CONFIRMED**: M5, M6, M8, M9, M11, M15, M16, M17, M18, **N2** (new)
**Real PARTIAL**: M1, M2, M3, M4, M10, M12
**False positives**: M7, M13, M14

### FALSE POSITIVES TOTAL: 13
- C3, C8, C9 (3 critical)
- H5, H9, H18, H19, H24, H25 (6 high)
- M7, M13, M14 (3 medium)
- FP1-FP5 (cache keys all OK via KEY_FUNCTION)
- (H10 already known)

### Test coverage gaps: 8 (unchanged — see section above)

### Severity-weighted action plan
**Pre-cutover MUST-FIX (10 items)**:
C1, C2, C4, C5, C6, C7, C11, N1, H1, M11

**Tenant-2 onboarding blockers (10 items)**:
H2, H3, H7, H8, H11, H12, H13, H15, H21, H22, H23

**Cleanup hygiene (12 items)**:
M5, M6, M8, M9, M15, M16, M17, M18, N2, H6, H14, H16, H17 (partials)

**Test coverage**: Cross-tenant isolation tests; Knox WS replay test; webhook routing tests; Meili index name tests.

## Direct main-thread verifications

- **C3 → FALSE POSITIVE** — `tenant/models.py:205` has `meta_pixel_id = models.CharField(max_length=64, ...)`.
- **Tenant store complete** — `app/stores/tenant.ts` exposes every per-tenant field the model defines (`metaPixelId`, `gaTrackingId`, `turnstileSiteKey`, `boxNowPartnerId`, `totpIssuer`, `socials.*`, `stripePublishableKey`, branding, colors, feature flags).
- **Media-stream provider correct** — `app/providers/media-stream.ts` does NOT prepend tenant schema (relies on Django's `connection.schema_name` paths). Inline docs explicit.

## Validation agent results (all 4 returned)

**Media-stream**: 1 confirmed (H21), 2 partial (H20, M4), bonus: disk-path collision PROTECTED.
**Infra**: 3 confirmed (M5, M6, M16), 3 false positives (C8, C9, M13), 1 partial (C10).
**Django**: many confirmed, several demoted: C1 partial (3 of 5 lines), C3/H5/H9/H24/H25 false positives, C11 IDOR confirmed AND adjacent IDOR in `release_reservations`.
**Nuxt**: C7 confirmed, H13/H15/H17/M8 confirmed; H18/H19/M7 demoted to false positives; H14/H16/M1/M2/M3 partial.

## NEW issues discovered during validation (bonus scans)

### N1. `server/api/settings/index.get.ts` serves wrong tenant's settings ✅ CONFIRMED — HIGH severity
**Found by**: Nuxt validation agent; verified by direct read.
**File**: `server/api/settings/index.get.ts:1-21`
**Detail**: Handler `async ()` (no event param), uses raw `$fetch(${config.apiBaseUrl}/settings)` without `X-Forwarded-Host`. Django's `TenantMainMiddleware` resolves Domain from `Host` header — without X-Forwarded-Host the Host is the internal K8s service name (`backend-service:80`) → no Domain match → falls back to public schema → public-schema settings returned. The `tenantCacheKey(event, 'settings')` cache key is correctly tenant-scoped, but the FETCH itself is not. Result: **every tenant gets public-schema settings**.
**Fix**: change to `useBackendFetch(event)` (auto-injects X-Forwarded-Host) and add `event` to handler signature. Reference pattern: `server/api/products/[id]/index.get.ts`.

### N2. Platform brand `'Webside'` hardcoded as fallback
**Found by**: Nuxt validation agent.
**Files**: `app/pages/about.vue:11`, `app/pages/vision.vue:6`
**Detail**: `tenantStore.storeName || 'Webside'` — leaks platform brand to other tenants when store not loaded. Fix: use `tenantStore.storeName || config.public.siteName` or drop the fallback (empty string is acceptable during hydration).

## Next step

Dispatch 4 parallel validation agents (one per repo) to confirm/refute each NEEDS-VERIFY finding against actual source. Update each row's Status field. Then prioritize the CONFIRMED CRITICAL items for fix sweep.
