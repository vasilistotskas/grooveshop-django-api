# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GrooveShop Django API — a headless, multi-tenant e-commerce API built with Django 6 and Django REST Framework. Served as ASGI only (Gunicorn managing Uvicorn workers; Daphne only backs `runserver`) with WebSocket notifications via Django Channels. Each store is a django-tenants schema resolved from the request host. Uses PostgreSQL 18, Redis, Celery (RabbitMQ broker), and Meilisearch. Python 3.14, managed with uv.

## Common Commands

```bash
# Install dependencies (uses uv, not pip)
uv sync --locked --all-extras --dev

# Run all tests (parallel by default via -n auto in addopts)
uv run pytest

# Run a single test file
uv run pytest tests/unit/path/to/test_file.py

# Run a single test function
uv run pytest tests/unit/path/to/test_file.py::test_function_name

# Run tests with coverage (must disable parallel with -n0)
uv run pytest --cov=. --cov-report=term --cov-report=html --cov-config=pyproject.toml -n0

# Lint and format
uv run ruff check --fix
uv run ruff format

# Type check (required before commit; not covered by pre-commit)
uv run ty check

# Run all pre-commit hooks
uv run pre-commit run --all-files

# Django management
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py runserver
uv run python manage.py seed_all

# Generate OpenAPI schema (used by Nuxt frontend for types)
uv run python manage.py spectacular --color --file schema.yml

# Translations: extract new messages into the Greek and English catalogues.
# The command writes the canonical form (sorted, no #: locations, pinned
# header) that tests/unit/test_locale_catalogue.py enforces, so parallel PRs
# adding different messages merge cleanly. Never hand-append to a .po, and
# rerun this after saving in Rosetta (it rewrites the file unsorted).
uv run python manage.py makemessages -l el -l en

# Meilisearch index management
uv run python manage.py meilisearch_sync_all_indexes
uv run python manage.py meilisearch_sync_index product.ProductTranslation

# Docker (full stack)
docker compose up -d --build
```

## Architecture

### Settings

Single `settings.py` at the project root (not inside any app). Uses `SYSTEM_ENV` env var (`dev`, `production`, `ci`) for environment-specific behavior. All config via `.env` file loaded with python-dotenv. The `DJANGO_SETTINGS_MODULE` is just `"settings"`.

### Django Apps

All apps live at the project root (flat structure, no `src/` directory):

- **core/** — Shared infrastructure: base views, serializers, permissions, middleware, filters, caching, Celery config, URL routing. The `core/urls.py` is the root URL conf that mounts all other apps under `api/v1/`.
- **tenant/** — Multi-tenancy control plane: `Tenant`/`TenantDomain`, provisioning, billing, lifecycle, staff identity and roles, per-tenant credentials (`tenant/credentials.py` — no platform-wide fallback for money/secrets; the secrets are Fernet-encrypted at rest, key setup and rotation in `docs/secret-encryption.md`). Read a per-tenant field through `tenant.membership.bound_tenant()`, never `connection.tenant`: under `schema_context` that is a bare `FakeTenant` with no fields, and `bound_tenant` returns the real row
- **product/**, **order/**, **cart/**, **blog/**, **user/** — Main domain apps
- **promotion/**, **giftcard/**, **b2b/** — Rule-based promotions and coupons; stored-value gift cards with a ledger; wholesale program (business profiles, VIES, group pricing)
- **shipping/**, **shipping_acs/**, **shipping_boxnow/** — Carrier abstraction (`shipping/interfaces.py`) and the two carrier integrations
- **page_config/** — Merchant-editable storefront layouts, navigation and content pages
- **texts/** — Store-edited storefront wording (`TextOverride`), the store admin's text editor, and the public overrides read; the catalogue and the syntax check are the storefront's
- **meta_capi/** — Meta Conversions API event dispatch
- **agent/** — OAuth-scoped `/api/v1/agent/*` surface consumed through the agent gateway
- **search/** — Meilisearch product and blog search, Greeklish transliteration, and analytics
- **meili/** — Meilisearch model definitions and indexing via `IndexMixin` with `MeiliMeta` config
- **notification/** — Real-time notifications via WebSocket (Django Channels consumer)
- **loyalty/** — Points, XP, tiers, and rewards system
- **country/**, **region/**, **vat/**, **pay_way/**, **tag/**, **contact/** — Supporting domain apps
- **admin/** — Custom admin app with django-unfold dashboard callback
- **devtools/** — `CustomDjangoModelFactory` base class for all test factories

### Core Base Classes and Patterns

**Abstract models** (all in `core/models.py`):
- `TimeStampMixinModel` — `created_at`/`updated_at` with indexes
- `UUIDModel` — UUID4 field for external/guest access
- `SeoTranslationModel` — `seo_title`, `seo_description`, `seo_keywords`, for a model's parler **translations** model (never the shared row): inherit it on an explicit `TranslatedFieldsModel`, or spread `**SeoTranslationModel.translated_fields()` into `TranslatedFields(...)`
- `SortableModel` — `sort_order` with atomic `move_up()`/`move_down()` using `select_for_update()`
- `PublishableModel` — `is_published`/`published_at` with `PublishableManager.published()` queryset
- `MetaDataModel` — `metadata`/`private_metadata` JSONFields with GinIndex
- `SoftDeleteModel` — `is_deleted`/`deleted_at` with `SoftDeleteManager` (`.all_with_deleted()`, `.deleted_only()`, `.restore()`, `.hard_delete()`)

Domain models compose multiple mixins, e.g. `Product(SoftDeleteModel, TranslatableModel, TimeStampMixinModel, UUIDModel, MetaDataModel, TaggedModel)` with `ProductTranslation(TranslatedFieldsModel, SeoTranslationModel, IndexMixin)`.

**Base ViewSet** (`core/api/views.py: BaseModelViewSet`):
- Combines `RequestResponseSerializerMixin`, `TranslationsModelViewSet`, `PaginationModelViewSet`
- Uses `serializers_config` dict mapping actions to `ActionConfig` objects (separate request/response serializers per action)
- Supports three pagination strategies via `?pagination_type=` query param: `pageNumber` (default), `cursor`, `limitOffset`
- Handles multipart translation data via `TranslationsProcessingMixin`
- Atomic transactions on create/update
- URL confs wire actions by hand (`ViewSet.as_view({"post": "retry_payment"})`), not through a router; `RouterActionOverridesMixin` (in `BaseModelViewSet`) applies each extra action's `@action(...)` arguments — `throttle_classes`, `permission_classes`, `pagination_class`, `queryset` — the way DRF's router would. A viewset not built on `BaseModelViewSet` must include it; `tests/unit/core/test_routes_apply_action_overrides.py` fails for any route that drops an override

**Optimized Managers** (`core/managers/`):
- `OptimizedManager`/`OptimizedQuerySet` — Override `for_list()` and `for_detail()` for select_related/prefetch_related
- `TranslatableOptimizedManager`/`TranslatableOptimizedQuerySet` — Adds `with_translations()` for parler
- `TreeTranslatableManager`/`TreeTranslatableQuerySet` — For MPTT + Parler models (categories)
- ViewSets call `Model.objects.for_list()` or `.for_detail()` based on action

**Composable FilterSets** (`core/filters/`):
- Mixin-based (`core/filters/core.py`): `TimeStampFilterMixin`, `UUIDFilterMixin`, `SortableFilterMixin`, `MetaDataFilterMixin` — each a real `FilterSet` subclass, because plain-class mixins were silently dropped by django-filter (see the module docstring)
- CamelCase variants (`core/filters/camel_case_filters.py`): `CamelCaseFilterMixin` auto-converts query param names; bases `CamelCaseTimeStampFilterSet` and `CamelCasePublishableTimeStampFilterSet`

**Pagination** (`core/pagination/`): `PageNumberPaginator`, `CursorPaginator`, `LimitOffsetPaginator` — all return consistent envelope: `{links, count, total_pages, page_size, page_total_results, page, results}`

**Serializer utilities** (`core/utils/serializers.py`):
- `ActionConfig` dataclass — per-action request/response serializer + OpenAPI metadata
- `crud_config()` — shorthand for standard CRUD serializer mapping
- `create_schema_view_config()` — auto-generates `@extend_schema` decorators from model verbose names

**Translation validation** (`core/api/serializers.py`): `RequiredDefaultTranslationMixin` — mix into a `TranslatableModelSerializer` and set `required_translation_field` to require the `settings.PARLER_DEFAULT_LANGUAGE_CODE` translation (with that field non-empty); other locales stay optional. Never hardcode a language code (e.g. `if "en" in value`) — the default is `el` and the locale set can change.

**Custom fields**: `ImageAndSvgField` (images + SVG), `MeasurementField` (physical measurements with unit conversion)

**Permissions** (`core/api/permissions.py`): `IsOwnerOrAdmin`, `IsOwnerOrAdminOrGuest` — checks `user`, `owner`, or `created_by` fields; the staff bypass is `tenant.membership.is_store_staff` (a provenance-stamped platform identity with a staff role in the current tenant, or a superuser). `request.user.is_staff` is never an authorization signal on an API request — on a tenant-schema row it is customer residue (see `IsPlatformSuperuser`). Store-scoped write routes use `StoreStaffModelPermissions`. `IsOwnerOrAdminOrGuest` additionally handles guest orders: when `obj.user is None`, it verifies `request.query_params.get("uuid") == str(obj.uuid)`.

### API Conventions

- All API endpoints are under `api/v1/` with DRF ViewSets
- Request/response bodies use **camelCase** (auto-converted from snake_case via `djangorestframework-camel-case`)
- Authentication: Knox token auth (`Bearer` prefix, 7-day TTL, auto-refresh after 1 day) + Django Allauth (account management, social providers: Google/Facebook/GitHub/Discord, MFA/WebAuthn/Passkeys) + Django session auth
- `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticatedOrReadOnly`. Any endpoint that requires anonymous POST access (e.g. guest checkout) must explicitly set `permission_classes = [AllowAny]`.
- Payments: Viva Wallet (primary) and Stripe via dj-stripe (secondary), plus cash on delivery; per-tenant credentials via `tenant/credentials.py`
- Default pagination: 12 items per page, max 100
- OpenAPI docs at `/api/v1/schema/swagger-ui` and `/api/v1/schema/redoc`
- Health check at `/api/v1/health` (checks DB, Redis, Celery)
- Serializer tiering: separate List/Detail/Write serializers per model

### Domain Patterns

- **Order / payment / shipping / notification system**: see [`docs/order-system.md`](docs/order-system.md) for the full reference — state machines, creation paths, carrier integrations, email + WS dedup, critical invariants, common-task playbook. Read this first before changing anything in `order/`, `shipping_acs/`, `shipping_boxnow/`, or `pay_way/`.
- **Product recommendations**: see [`docs/recommendations-engine.md`](docs/recommendations-engine.md) — typed `ProductRelation`, the strategy registry (`is_available` declines rather than returning noise), two-stage retrieval into `RecommendationCandidate`, the Greek embedding measurements and the TEI-vs-in-process decision, plan tiers, and the playbook for adding a strategy/surface/preset. Read it before touching `recommendation/`, `product/models/relation.py`, or the cart `recommendations` fields.
- **Product pricing and the EU Omnibus prior price**: see [`docs/product-pricing.md`](docs/product-pricing.md) — price spells, the frozen reduction episode on `Product`, every path that records a price change, the backfill, and the `priorPrice` / `announcedDiscountPercent` API fields. Read it before touching `Product.save`, `final_price`, VAT rates, or what a promotion does to a listed price; a price field must never change through `queryset.update()`.
- **Staging refresh anonymisation**: `manage.py staging_anonymise` (`devtools/anonymise/`) replaces every customer identity in a database restored from production, staging only. See [`docs/staging-anonymise.md`](docs/staging-anonymise.md). A new model must be given a `Scrub`, a `Delete` or a `NOT_PERSONAL` entry in `devtools/anonymise/rules.py`, and a new e-mail/phone/IP field a rule or a `NOT_PERSONAL_FIELDS` reason; `tests/unit/devtools/test_anonymise_covers_every_model.py` fails until you do.
- **Store settings (`core/store_settings.py`)**: read every django-extra-settings value through `store_setting(name)` / `store_settings(names)`, never `Setting.get` with a literal default. A missing row reads as its value in `settings.EXTRA_SETTINGS_DEFAULTS`, the one place a default lives, and an undeclared name raises. Validate a setting where it is saved (a `"validator"` path on its declaration, synced to every store on migrate by `tenant/settings_metadata.py`) instead of replacing a bad value at the read. Tests patch `extra_settings.models.Setting.get` or write rows, never a module's own reader.
- **Store features (`tenant/features.py`)**: see [`docs/store-features.md`](docs/store-features.md) — the one table behind `TenantConfig.features` and the 404 gates, the classification of every `*_ENABLED` setting, and the checklist for adding a feature. Read it before adding a switch a client has to honour.
- **Store-editable texts (`texts/`)**: see [`docs/store-texts.md`](docs/store-texts.md) — the storefront owns the catalogue and the message syntax, Django asks it (`texts/storefront.py`) and refuses a save it cannot check; `texts/services.py` is the only writer; a save moves the cache generation and the `store_texts` surface. Read it before touching `texts/` or adding a text surface (emails).
- **API error contract (`core/api/exception_handler.py`)**: see [`docs/error-contract.md`](docs/error-contract.md) — every raised error carries a stable `code` (and validation errors a per-field `fields`), codes are passed as `code=` and never through `_()`, and which modules are coded so far. Read it before adding a `ValidationError`/`APIException` a client has to react to.
- **Platform-wide documentation** lives in the sibling `grooveshop-docs/` repository (published at `docs.grooveshop.space`): architecture, multi-tenancy, end-to-end flows and operations under `src/dev/`, the bilingual tenant-admin guide under `src/admin/`. The files in this repo's `docs/` folder are copied into that site at build time, so keep editing them here. A behaviour change that a docs page describes ships with the doc change.
- **Client IP and throttle identity (`core/client_ip.py`)**: never read `REMOTE_ADDR`, `X-Forwarded-For` or `X-Real-IP` directly to identify a caller; use `trusted_client_ip(request)` or `client_ip_or_peer(request)`. Read [`docs/client-ip.md`](docs/client-ip.md) before touching throttles or anything keyed on an address.
- **Transactional email rendering (`core/templates/emails/**`)**: admin-authored WYSIWYG/HTML model fields (e.g. `PayWay.instructions` — a dedicated `tinymce.models.HTMLField`, picked up by `BaseModelAdmin.formfield_overrides` rather than a blanket `TextField`→`WysiwygWidget` override, so it holds HTML) must render as `{{ field|safe }}` in `.html` templates and `{{ field_text|safe }}` in `.txt` templates, where `field_text` is pre-built in the email task via `unescape(strip_tags(field))`. Django **autoescapes `.txt` templates too**, so the stripped plain text must be marked `|safe` or it re-encodes `& < >` back into entities. Escaping such a field (e.g. `|linebreaksbr`) renders literal `<div>` tags to the customer.
- **Translations**: django-parler `TranslatableModel` on Product, BlogPost, Category, LoyaltyTier, etc. Languages: el (default), en. Factories create translations for all languages.
- **Audit history**: django-simple-history on models
- **Tree structures**: django-mptt `TreeForeignKey` for ProductCategory, BlogCategory
- **Monetary fields**: django-money `MoneyField` for prices, totals. Default currency: EUR
- **Stock management**: `StockManager` with atomic `reserve_stock()`/`release_stock()` using `select_for_update()` to prevent overselling under concurrent requests. `StockReservation` model with TTL (the store's `STOCK_RESERVATION_TTL_MINUTES`). `StockLog` for audit trail.
- **Computed properties**: Models check `__dict__` for annotation values before falling back to DB queries (e.g. `likes_count`, `review_average`)
- **Meilisearch indexing**: Models inherit `IndexMixin` with `MeiliMeta` class defining filterable/searchable/sortable fields, ranking rules, synonyms, typo tolerance. `meili_filter()` controls indexing eligibility. `get_additional_meili_fields()` adds computed fields.
- **Greeklish search**: handled at *index time* (`search/transliteration.py`); queries reach Meilisearch verbatim and are never rewritten into variants. Read [`docs/search/greeklish.md`](docs/search/greeklish.md) before touching transliteration, `MeiliMeta` search settings or `search/views.py`.

### WebSocket / Real-time

ASGI routing in `asgi/__init__.py` with Channels `ProtocolTypeRouter`:
- HTTP: Django ASGI. CORS is django-cors-headers only: the static `CORS_ALLOWED_ORIGINS` holds the platform origins and `tenant.signals.allow_tenant_origin` admits the current tenant's own domains (the same rule as `TenantCsrfMiddleware`); never set `CORS_ALLOW_ALL_ORIGINS`
- WebSocket: `ws/notifications/` → `NotificationConsumer`
- Auth via `TokenAuthMiddleware` — a single-use `?ticket=<token>` minted by the API and consumed atomically from Redis (`core/middleware/channels.py`); Knox tokens and session cookies are not accepted on the socket
- Groups: `tenant_{schema}_user_{id}` per user (`notification/groups.py`); WebSocket identities are tenant-schema customers, so there is no staff group on the socket

### Celery

App configured in `core/celery.py`. Base task class: `MonitoredTask` (logs success/failure). Retry policy is per task, never global: Celery has no setting for `autoretry_for`, backoff or `max_retries` (a `CELERY_TASK_AUTORETRY_FOR` in `settings.py` is read by nothing), so each decorator sets `autoretry_for`, `retry_backoff`, `retry_jitter` and its own `max_retries` (2 to 5; Celery defaults to 3). `autoretry_for` names a transient set from `core/retry.py` (`TRANSIENT_ERRORS`, `MAIL_TRANSIENT_ERRORS`, `SEARCH_TRANSIENT_ERRORS`, `TRANSIENT_HTTP_ERRORS`), never `Exception`: a deterministic failure retried only repeats itself. `MonitoredTask.retry` also stops at once on an SMTP 5xx or an HTTP 4xx, which no class can express. `tests/unit/core/test_celery_tasks_do_not_retry_everything.py` fails for any registered task whose `autoretry_for` contains `Exception`.

Tasks are split by domain:
- `core/tasks.py` — system health monitoring, DB backup, cache clearing, session cleanup, Meilisearch sync, abandoned cart cleanup, inactive user notifications
- `product/tasks.py` — price drop notifications, price-record reconciliation (`reevaluate_price_episodes`)
- `search/tasks.py` — search analytics
- `blog/tasks.py` — comment liked notifications
- `loyalty/tasks.py` — loyalty point award/expiry tasks (all use `MonitoredTask` base class)
- `contact/tasks.py` — notification emails, attachment scanning, and the retention sweeps: `purge_expired_attachment_files` (bytes, `CONTACT_ATTACHMENTS_RETENTION_DAYS`) and `purge_expired_contact_records` (deletes enquiries, their files, and feedback older than the per-store `CONTACT_RETENTION_DAYS`, default 365, `0` keeps). Both run per tenant through `tenant.tasks.fanout_*` with `include_suspended=True`

**Nightly dump switch.** `DATABASE_BACKUPS_ENABLED` (env, default `True`) keeps
`scheduled-database-backup` and `cleanup-old-backups` in the beat schedule;
staging sets it `False`. `DatabaseScheduler` never deletes a row whose entry
left `CELERY_BEAT_SCHEDULE`, so `core/beat.py` deletes the rows named by
`CELERY_BEAT_UNSCHEDULED_ENTRIES` on every beat start. See
`docs/staging-anonymise.md`.

**Only task FAILURES are stored** (`CELERY_TASK_IGNORE_RESULT`), in the public schema's `django_celery_results`, tagged with the store by `core.result_backend.TenantDatabaseBackend`. A `chord()`/`group()` needs `ignore_result=False` on that task; "did it run?" is `PeriodicTask.last_run_at`, never that table. Detail: [`docs/celery-results.md`](docs/celery-results.md).

### Factory / Test Data Pattern

All factories extend `CustomDjangoModelFactory` from `devtools/factories.py`. Key features:
- `auto_translations` flag for parler models
- `unique_model_fields` list for collision-free field generation
- `@factory.post_generation` for related objects (images, reviews, translations, cart items)
- `get_or_create_instance()` helper in `core/helpers/factory.py` for dynamic model/factory resolution
- `UserNameGenerator` in `core/generators.py` generates `{Adjective}{Noun}#{hash}` usernames from email

### Test Configuration

Tests in `tests/` (`unit/`, `integration/`, `utils/`) and the multi-tenant lane `tests_mt/` (run it with `uv run pytest tests_mt -n 0`). A second parallel run beside a full one needs its own `DB_NAME`. Read [`docs/testing.md`](docs/testing.md) before writing a test that commits, touches seeds, settings or the cache, or before changing `tests/settings.py` / a `conftest.py`.

### Infrastructure (Docker)

- `infra.compose.yml` — PostgreSQL 18, Redis, RabbitMQ, Meilisearch, pgAdmin, RedisInsight
- `app.compose.yml` — backend-init (migrations), backend, celery_worker, celery_beat, celery_flower
- `docker-compose.yml` — Combines both on `grooveshop-backbone` bridge network
- `Dockerfile` — Multi-stage Alpine build (uv → tailwind CSS → Python deps → production)
- `dev.Dockerfile` — Debian slim with full dev environment

**The dev stack bind-mounts your working tree.** `app.compose.yml` mounts `.`
over `/home/app`, so `manage.py runserver`'s autoreloader sees your edits and
restarts. Two consequences worth knowing:

- **The virtualenv lives at `/opt/venv`, not `/home/app/.venv`**
  (`UV_PROJECT_ENVIRONMENT`), with uv's cache at `/opt/uv-cache`. Both sit
  outside the bind so the host's `.venv` — a *Windows* virtualenv on a Windows
  host — cannot shadow the container's. Anonymous volumes over those paths
  would also work, but compose REUSES anonymous volumes across recreates, so
  the first dependency bump would leave a stale env masking the rebuilt image.
- **Rebuild every app service together**, not just `backend`:
  `docker compose build backend backend-init celery_worker celery_beat celery_flower`.
  They share `dev.Dockerfile`, and a half-rebuilt stack fails in a way that
  reads like a permissions bug (`backend-init` on the old image cannot write
  `/home/app/.cache/uv`, which is now your host checkout).

If the container seems to ignore a change, confirm it is actually running your
code — `docker compose exec backend grep <something> path/to/file.py` — before
debugging the code.

### Migrations: the release still serving reads the new schema

The Argo CD PreSync hook migrates before the new image rolls, so the
previous release keeps querying the migrated schema until its last pod
exits. **A migration may only add.** Anything that removes, renames or
tightens ships in two releases: release N stops using the old shape and
takes it out of state with `SeparateDatabaseAndState` (giving every
column the new code stops writing a `db_default` or `null=True`);
release N+1, once N runs in production, drops it with a `RunSQL` whose
migration declares `contract_of = [("<app>", "<expand migration>")]`.
Every new NOT NULL column on an existing table needs `db_default` — a
plain `default=` is dropped right after `ADD COLUMN`.
`manage.py migration_preflight` enforces this in CI and as PreSync step
0; `accepted_downtime = "<reason>"` on a migration is the reported
escape hatch. Full rule and the worked SEO example: `docs/migrations.md`.

### API fields follow the same rule, storefront first

The storefront parses every response with Zod schemas generated from
`schema.yml` and answers 422 to anything they reject, so **removing,
renaming or loosening (optional, nullable, new enum value) a response
field breaks the storefront that is live**. Ship it in two steps: a
storefront release that no longer requires the field reaches production,
then the Django release that changes it. On 2026-10-13 the reverse order
took add to cart down on webside for ~8 minutes. Three checks enforce it:
CI's "OpenAPI schema.yml is up to date" step (the committed `schema.yml`
must match the code structurally — regenerate it with
`uv run python manage.py spectacular --file schema.yml`), the
`API contract` workflow (a PR that breaks clients of the base fails unless
labelled `api-breaking-change`), and the deploy gate in
`grooveshop-infrastructure` that refuses a deploy pairing a Django release
with a storefront it breaks.

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) jobs:
0. **Already tested in its PR** — on a push to `main` whose tree is exactly what a green pull_request run tested (the PR branch was up to date with `main`), jobs 2-6 are skipped and release starts at once (`.github/scripts/tested_in_pr.sh` holds the rule). Any other push runs everything. Merging a branch that is behind `main` therefore costs a full second run; updating it first does not.
1. **Security Scan** — Trivy filesystem scan (HIGH/CRITICAL fail the job).
2. **Code Quality** — Ruff format + lint, `ty check`, OpenAPI schema validation, and the committed `schema.yml` matching the code (structurally, via oasdiff).
3. **Migration Check** — replays a deploy: migrates at the base commit, then runs the head's `makemigrations --check` and `migration_preflight`, then both django-tenants migration paths on top.
4. **Testing** — 4 duration-balanced pytest shards (pytest-split, `.test_durations`) against PostgreSQL 18 + Redis + Meilisearch, each uploading raw coverage data.
5. **Testing (multi-tenant lane)** — `tests_mt` serially with the real tenant router and middleware.
6. **Coverage Gate** — combines the shard data and enforces `fail_under`.
7. **Release** — python-semantic-release (run from the locked dev group) cuts the version, changelog and GitHub release on `main`; its version commit also carries the re-locked `uv.lock` (`build_command` + `assets` in `pyproject.toml`), so `main` never holds a stale lock. The Docker image workflow triggers on the published release. Nothing is built or published to PyPI — this is a deployed application, not a library.

`.github/workflows/api-contract.yml` is separate: on a PR that changes `schema.yml` it runs `oasdiff breaking` from the base branch's schema to the PR's and fails on a break unless the PR has the `api-breaking-change` label (its own workflow so that toggling the label re-runs only it).

## Code Style

- **Line length**: 80 characters
- **Formatter/Linter**: Ruff (`target-version = "py314"`)
- **Type checker**: `ty` (config under `[tool.ty]`). Not in pre-commit — run
  `uv run ty check` yourself before committing; the `.claude` PostToolUse hook
  reports per-file findings as you edit but never blocks.
- **Max POSITIONAL args**: 6 (`PLR0917` via ruff). Deliberately not
  `PLR0913`, which counts every parameter and so penalises
  keyword-only arguments — the very shape that removes the hazard,
  since a keyword argument cannot be mixed up at a call site.
  Exempt in `tests/`: pytest injects fixtures positionally and
  `@mock.patch` passes each mock as a positional argument.
- Migrations are excluded from linting (`**/migrations/**`)
- Semantic release: `feat` → minor, `fix`/`perf` → patch

## MCP servers

No `.mcp.json` is committed. Context7 is configured once per machine at user
scope, with your own key read from the environment:

```bash
claude mcp add-json --scope user context7 '{"type":"http","url":"https://mcp.context7.com/mcp","headers":{"Authorization":"Bearer ${CONTEXT7_API_KEY}"}}'
```

A project `.mcp.json` entry with the same name would take precedence over that
server, which is why none is committed here.

**IDE bridges belong in user scope, not here.** A JetBrains MCP endpoint is a
machine- and session-specific `http://127.0.0.1:<ephemeral-port>/stream`; a
committed entry fails to connect for everyone else and reports an error on every
session start. Add yours to your own config instead:

```bash
claude mcp add --scope user --transport http pycharm "http://127.0.0.1:<port>/stream"
```

The same applies to anything that points at a local tool, a personal token, or a
path under your home directory.
