# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GrooveShop Django API — a headless, multi-tenant e-commerce API built with Django 6 and Django REST Framework. Served as ASGI only (Gunicorn managing Uvicorn workers in production, Uvicorn `--reload` locally) with WebSocket notifications via Django Channels. Each store is a django-tenants schema resolved from the request host. Uses PostgreSQL 18, Redis, Celery (RabbitMQ broker), and Meilisearch. Python 3.15, managed with uv.

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
uv run pytest --cov=src --cov-report=term --cov-report=html --cov-config=pyproject.toml -n0

# Lint and format
uv run ruff check --fix
uv run ruff format

# Type check (required before commit; not covered by pre-commit)
uv run ty check

# Import contracts between apps ([tool.importlinter] in pyproject.toml).
# A contract's ignore_imports only shrinks: fix the import, delete its line.
# lint-imports puts only the working directory on the path, hence PYTHONPATH.
PYTHONPATH=src uv run lint-imports

# Run all pre-commit hooks
uv run pre-commit run --all-files

# Django management
uv run python manage.py makemigrations
uv run python manage.py migrate
PYTHONPATH=src uv run uvicorn asgi:application --reload   # dev server: HTTP + WebSocket, serves /static and /media in DEBUG
# `manage.py runserver` is not the dev server: it serves plain HTTP through
# Django's default WSGI handler but no WebSocket (ws/notifications/)
uv run python manage.py seed_all

# Generate OpenAPI schema (used by Nuxt frontend for types). The output is
# the same on every machine (nothing in it comes from .env); the one thing
# that varies is the language of descriptions, so compile the catalogues first
# (`manage.py compilemessages`) or the diff shows Greek/English prose churn.
uv run python manage.py spectacular --color --file schema.yml

# Translations: extract new messages into the Greek and English catalogues.
# The command writes the canonical form (sorted, no #: locations, pinned
# header) that tests/unit/test_locale_catalogue.py enforces, so parallel PRs
# adding different messages merge cleanly. Never hand-append to a .po, and
# rerun this after saving in Rosetta (it rewrites the file unsorted).
uv run python manage.py makemessages -l el -l en
# It leaves each NEW English entry empty or fuzzy: give it msgstr = msgid and
# drop the fuzzy flag, or tests/unit/test_english_catalogue.py fails (shard 4).
# Run that file with test_locale_catalogue.py after every makemessages.

# Meilisearch index management
uv run python manage.py meilisearch_sync_all_indexes
uv run python manage.py meilisearch_sync_index product.ProductTranslation

# Docker (full stack)
docker compose up -d --build
```

## Architecture

### Settings

Single `src/settings.py` (not inside any app). `SRC_DIR` is `src/`, for what Django loads from the source (`src/core/templates`, `src/locale`, `src/locale_vendor`, `src/static`); `BASE_DIR` is the repository root (`/home/app` in the image), for `.env`, `pyproject.toml` and what is written at run time (`logs`, `backups`, `mediafiles`, `staticfiles`). Uses `SYSTEM_ENV` env var (`dev`, `ci`, `staging`, `production`) for environment-specific behavior. All config via `.env` file loaded with python-dotenv. The `DJANGO_SETTINGS_MODULE` is just `"settings"`.

### Django Apps

Every app, the settings module (`src/settings.py`) and the `asgi` package live in `src/`, and `src/` is on the Python path, so an import names the app and never `src` (`from order.services import ...`, `DJANGO_SETTINGS_MODULE=settings`, `celery -A core`, `asgi:application`). A file is named by its path (`src/order/services.py`), a module by its dotted name (`order.services`). What puts `src/` on the path: `manage.py` and `gunicorn.conf.py` add it themselves, pytest reads `pythonpath` in `pyproject.toml`, and both images set `PYTHONPATH`. Any other command run on a checkout needs `PYTHONPATH=src` (`lint-imports`, `uvicorn`, `celery`). The repository root keeps the entry points, the tooling and its configuration, `tests/`, `tests_mt/`, `docs/`, and the folders written at run time.

**The apps are listed in [`docs/app-map.md`](docs/app-map.md)**: what each owns, its entry points, and the doc to read before changing it. Read the entry of the app you are about to touch. What to know before that:

- **Two app lists** (`src/settings.py`): `SHARED_APPS` live in the `public` schema (`core`, `tenant`, `scheduler`, `country`, `region`, `devtools`), `TENANT_APPS` in every store's schema (the domain apps), and `user`, `emails` and `admin` are in both. The list decides which host can serve an app.
- **`core/`** is shared infrastructure (base views, serializers, permissions, middleware, filters, caching, the Celery app); `src/core/urls.py` is the root URL conf that mounts every app under `api/v1/`.
- **`tenant/`** is the control plane. Read a per-tenant field through `tenant.membership.bound_tenant()`, never `connection.tenant`: under `schema_context` that is a bare `FakeTenant` with no fields, and `bound_tenant` returns the real row. Per-tenant credentials come from `src/tenant/credentials.py`, with no platform-wide fallback for money or secrets (`docs/secret-encryption.md`).
- **Layout conventions**: a module becomes a package at about 500 lines; tasks live in the app's `tasks`, receivers in its `signals`, connected from `AppConfig.ready()`; imports between apps follow the contracts in `pyproject.toml` (`uv run lint-imports`).

### Core Base Classes and Patterns

**Abstract models** (all in `src/core/models.py`):
- `TimeStampMixinModel` — `created_at`/`updated_at` with indexes
- `UUIDModel` — UUID4 field for external/guest access
- `SeoTranslationModel` — `seo_title`, `seo_description`, `seo_keywords`, for a model's parler **translations** model (never the shared row): inherit it on an explicit `TranslatedFieldsModel`, or spread `**SeoTranslationModel.translated_fields()` into `TranslatedFields(...)`
- `SortableModel` — `sort_order` with atomic `move_up()`/`move_down()` using `select_for_update()`
- `PublishableModel` — `is_published`/`published_at` with `PublishableManager.published()` queryset
- `MetaDataModel` — `metadata`/`private_metadata` JSONFields with GinIndex
- `SoftDeleteModel` — `is_deleted`/`deleted_at` with `SoftDeleteManager` (`.all_with_deleted()`, `.deleted_only()`, `.restore()`, `.hard_delete()`)

Domain models compose multiple mixins, e.g. `Product(SoftDeleteModel, TranslatableModel, TimeStampMixinModel, UUIDModel, MetaDataModel, TaggedModel)` with `ProductTranslation(TranslatedFieldsModel, SeoTranslationModel, IndexMixin)`.

**Base ViewSet** (`src/core/api/views.py: BaseModelViewSet`):
- Combines `RequestResponseSerializerMixin`, `TranslationsModelViewSet`, `PaginationModelViewSet`
- Uses `serializers_config` dict mapping actions to `ActionConfig` objects (separate request/response serializers per action)
- Supports three pagination strategies via `?pagination_type=` query param: `pageNumber` (default), `cursor`, `limitOffset`
- Handles multipart translation data via `TranslationsProcessingMixin`
- Atomic transactions on create/update
- URL confs wire actions by hand (`ViewSet.as_view({"post": "retry_payment"})`), not through a router; `RouterActionOverridesMixin` (in `BaseModelViewSet`) applies each extra action's `@action(...)` arguments — `throttle_classes`, `permission_classes`, `pagination_class`, `queryset` — the way DRF's router would. A viewset not built on `BaseModelViewSet` must include it; `tests/unit/core/test_routes_apply_action_overrides.py` fails for any route that drops an override

**Optimized Managers** (`src/core/managers/`):
- `OptimizedManager`/`OptimizedQuerySet` — Override `for_list()` and `for_detail()` for select_related/prefetch_related
- `TranslatableOptimizedManager`/`TranslatableOptimizedQuerySet` — Adds `with_translations()` for parler
- `TreeTranslatableManager`/`TreeTranslatableQuerySet` — For MPTT + Parler models (categories)
- ViewSets call `Model.objects.for_list()` or `.for_detail()` based on action

**Composable FilterSets** (`src/core/filters/`):
- Mixin-based (`src/core/filters/core.py`): `TimeStampFilterMixin`, `UUIDFilterMixin`, `SortableFilterMixin`, `MetaDataFilterMixin` — each a real `FilterSet` subclass, because plain-class mixins were silently dropped by django-filter (see the module docstring)
- CamelCase variants (`src/core/filters/camel_case_filters.py`): `CamelCaseFilterMixin` auto-converts query param names; bases `CamelCaseTimeStampFilterSet` and `CamelCasePublishableTimeStampFilterSet`

**Pagination** (`src/core/pagination/`): `PageNumberPaginator`, `CursorPaginator`, `LimitOffsetPaginator` — all return consistent envelope: `{links, count, total_pages, page_size, page_total_results, page, results}`

**Serializer utilities** (`src/core/utils/serializers.py`):
- `ActionConfig` dataclass — per-action request/response serializer + OpenAPI metadata
- `crud_config()` — shorthand for standard CRUD serializer mapping
- `create_schema_view_config()` — auto-generates `@extend_schema` decorators from model verbose names

**Translation validation** (`src/core/api/serializers.py`): `RequiredDefaultTranslationMixin` — mix into a `TranslatableModelSerializer` and set `required_translation_field` to require the `settings.PARLER_DEFAULT_LANGUAGE_CODE` translation (with that field non-empty); other locales stay optional. Never hardcode a language code (e.g. `if "en" in value`) — the default is `el` and the locale set can change.

**Custom fields**: `ImageAndSvgField` (images + SVG), `MeasurementField` (physical measurements with unit conversion)

**Permissions** (`src/core/api/permissions.py`): `IsOwnerOrAdmin`, `IsOwnerOrAdminOrGuest` — checks `user`, `owner`, or `created_by` fields; the staff bypass is `tenant.membership.is_store_staff` (a provenance-stamped platform identity with a staff role in the current tenant, or a superuser). `request.user.is_staff` is never an authorization signal on an API request — on a tenant-schema row it is customer residue (see `IsPlatformSuperuser`). Store-scoped write routes use `StoreStaffModelPermissions`. `IsOwnerOrAdminOrGuest` additionally handles guest orders: when `obj.user is None`, it verifies `request.query_params.get("uuid") == str(obj.uuid)`.

### API Conventions

- All API endpoints are under `api/v1/` with DRF ViewSets
- Request/response bodies use **camelCase** (auto-converted from snake_case via `djangorestframework-camel-case`)
- Authentication: Knox token auth (`Bearer` prefix, 7-day TTL, auto-refresh after 1 day) + Django Allauth (account management, social providers: Google/Facebook/GitHub/Discord, MFA/WebAuthn/Passkeys) + Django session auth
- `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticatedOrReadOnly`. Any endpoint that requires anonymous POST access (e.g. guest checkout) must explicitly set `permission_classes = [AllowAny]`.
- Payments: Viva Wallet (primary) and Stripe via dj-stripe (secondary), plus cash on delivery; per-tenant credentials via `src/tenant/credentials.py`
- Default pagination: 12 items per page, max 100
- OpenAPI docs at `/api/v1/schema/swagger-ui` and `/api/v1/schema/redoc`
- Health check at `/api/v1/health` (checks DB, Redis, Celery)
- Serializer tiering: separate List/Detail/Write serializers per model

### Domain Patterns

The per-domain guides (orders, shipping, pricing, emails, store settings and
features, builder, uploads, rich text, search, client IP, the error contract) live
in `.claude/rules/domain-patterns.md`, which loads when Claude touches a Python
file or a template. Read it directly before designing a change from outside those
files.

### WebSocket / Real-time

ASGI routing in `src/asgi/__init__.py` with Channels `ProtocolTypeRouter`:
- HTTP: Django ASGI. CORS is django-cors-headers only: the static `CORS_ALLOWED_ORIGINS` holds the platform origins and `tenant.signals.allow_tenant_origin` admits the current tenant's own domains (the same rule as `TenantCsrfMiddleware`); never set `CORS_ALLOW_ALL_ORIGINS`
- WebSocket: `ws/notifications/` → `NotificationConsumer`
- Auth via `TokenAuthMiddleware` — a single-use `?ticket=<token>` minted by the API and consumed atomically from Redis (`src/core/tickets.py`, spent in `src/core/middleware/channels.py`); Knox tokens and session cookies are not accepted on the socket
- Groups: `tenant_{schema}_user_{id}` per user (`src/notification/groups.py`); WebSocket identities are tenant-schema customers, so there is no staff group on the socket

### Celery

App configured in `src/core/celery.py`. Base task class: `MonitoredTask` in `src/core/tasks.py` (logs success/failure). Retry policy is per task, never global: Celery has no setting for `autoretry_for`, backoff or `max_retries`, so each decorator sets `autoretry_for`, `retry_backoff`, `retry_jitter` and its own `max_retries` (2 to 5; Celery defaults to 3). `autoretry_for` names a transient set from `src/core/retry.py` (`TRANSIENT_ERRORS`, `MAIL_TRANSIENT_ERRORS`, `SEARCH_TRANSIENT_ERRORS`, `TRANSIENT_HTTP_ERRORS`), never `Exception`: a deterministic failure retried only repeats itself. `MonitoredTask.retry` also stops at once on an SMTP 5xx or an HTTP 4xx, which no class can express. `tests/unit/core/test_celery_tasks_do_not_retry_everything.py` fails for any registered task whose `autoretry_for` contains `Exception`.

Tasks are split by domain:
- `src/core/tasks.py` — system health monitoring, DB backup, cache clearing, session cleanup, Meilisearch sync, abandoned cart cleanup, inactive user notifications
- `src/product/tasks.py` — price drop notifications, price-record reconciliation (`reevaluate_price_episodes`)
- `src/search/tasks.py` — search analytics
- `src/blog/tasks.py` — comment liked notifications
- `src/loyalty/tasks.py` — loyalty point award/expiry tasks (all use `MonitoredTask` base class)
- `src/contact/tasks.py` — notification emails, attachment scanning, and the retention sweeps: `purge_expired_attachment_files` (bytes, `CONTACT_ATTACHMENTS_RETENTION_DAYS`) and `purge_expired_contact_records` (deletes enquiries, their files, and feedback older than the per-store `CONTACT_RETENTION_DAYS`, default 365, `0` keeps). Both run per tenant through `tenant.tasks.fanout_*` with `include_suspended=True`

**Queueing after a commit.** Queue a task that follows a change with `tenant.celery.dispatch_on_commit` (schema pinned at the call). A publish that fails because the broker is unreachable never reaches the caller: it is logged at ERROR and kept as a `tenant.DeferredDispatch` that `redispatch_deferred_tasks` publishes again; a task whose work is the state of a row also gets a sweep over that state (`contact.tasks.rescan_pending_attachments`). See [`docs/deferred-dispatch.md`](docs/deferred-dispatch.md).

**Data retention.** Every sweep deletes through `core.retention.delete_in_batches` (batch size `RETENTION_PURGE_BATCH_SIZE`) and per-store tables fan out through `tenant.tasks.fanout_purge_*` with `include_suspended=True`. A model whose `HistoricalRecords` sets `excluded_fields` and is saved by a bookkeeping task uses `core.history.ChangeOnlyHistoricalRecords`, or every poll tick writes a history row. The full table (what, how long, which setting) is `grooveshop-docs/src/dev/operations/data-retention.md`.

**The schedule is code (`src/scheduler/`).** See [`docs/scheduler.md`](docs/scheduler.md). `CELERY_BEAT_SCHEDULE` is the whole schedule, and `scheduler.beat.CodeOwnedScheduler`, a `celery.beat.Scheduler` subclass, reads it. A `scheduler.PeriodicTask` row holds only state (enabled, last run, run count), and beat deletes any row the settings do not declare when it starts, so removing an entry needs no migration. The platform admin reads the schedule, switches an entry off or on and runs one by hand; to change when a job runs, change the settings and deploy.

**Nightly dump switch.** `DATABASE_BACKUPS_ENABLED` (env, default `True`) keeps
`scheduled-database-backup` and `cleanup-old-backups` in the beat schedule;
staging sets it `False`. See `docs/staging-anonymise.md`.

**Only task FAILURES are stored** (`CELERY_TASK_IGNORE_RESULT`), in the public schema's `django_celery_results`, tagged with the store by `core.result_backend.TenantDatabaseBackend`. A `chord()`/`group()` needs `ignore_result=False` on that task; "did it run?" is `PeriodicTask.last_run_at`, never that table. Detail: [`docs/celery-results.md`](docs/celery-results.md).

### Factory / Test Data Pattern

Factories of translatable models extend `CustomDjangoModelFactory` from `src/devtools/factories.py`; the rest use plain `factory.django.DjangoModelFactory`. Key features:
- `auto_translations` flag for parler models
- `unique_model_fields` list for collision-free field generation
- `@factory.post_generation` for related objects (images, reviews, translations, cart items)
- `get_or_create_instance()` helper in `src/core/helpers/factory.py` for dynamic model/factory resolution
- `UserNameGenerator` in `src/core/generators.py` generates `{Adjective}{Noun}#{hash}` usernames from email

### Test Configuration

Tests in `tests/` (`unit/`, `integration/`, `utils/`) and the multi-tenant lane `tests_mt/` (run it with `uv run pytest tests_mt -n 0`). A second parallel run beside a full one needs its own `DB_NAME`. Read [`docs/testing.md`](docs/testing.md) before writing a test that commits, touches seeds, settings or the cache, or before changing `tests/settings.py` / a `conftest.py`.

**What to run before pushing.** A small PR runs the static gates (`ruff`, `ty check`, `lint-imports`) and the tests of what it touched; CI runs the full suite. Run the full suite locally only for a wide change (settings, a `conftest.py`, a shared base class, moved code) or when CI is unavailable.

### Infrastructure (Docker)

- `infra.compose.yml` — PostgreSQL 18, Redis, RabbitMQ, Meilisearch, pgAdmin, RedisInsight
- `app.compose.yml` — backend-init (migrations), backend, celery_worker, celery_beat, celery_flower
- `docker-compose.yml` — Combines both on `grooveshop-backbone` bridge network
- `Dockerfile` — Multi-stage Alpine build (uv → tailwind CSS → Python deps → production)
- `dev.Dockerfile` — Debian slim with full dev environment

**The dev stack bind-mounts your working tree.** `app.compose.yml` mounts `.`
over `/home/app`, so Uvicorn's `--reload` watcher sees your edits and
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
`API contract` workflow (a PR whose `schema.yml` or `allauth-schema.yml`
cannot be live together with the storefront and gateway that production and
staging run NOW fails, so a change with no safe deploy order has to be
split), and the deploy gate in `grooveshop-infrastructure` that refuses a
deploy pairing a Django release with a storefront it breaks. The last two
run the same script and rules, in `api-contract/` (read its README before
touching `schema.yml`'s response shapes): an accepted break is a dated
`until` line in `api-contract/ignore.txt`, added in the PR that makes the
change.

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`) jobs:
0. **Already tested in its PR** — on a push to `main` whose tree is exactly what a green pull_request run tested (the PR branch was up to date with `main`), jobs 2-6 are skipped and release starts at once (`.github/scripts/tested_in_pr.sh` holds the rule). Any other push runs everything. So a branch that is behind `main` may be merged as it is when its files do not overlap what landed on `main` since it branched: `main` then runs the full suite before it releases. Nothing untested is released; the cost is that `main` can be red for that one run. Update the branch first when the files overlap, or when either side touched a migration, `schema.yml` or `uv.lock`. A new push cancels the superseded run of a pull request; a run on `main` is never cancelled and never waits for another. Its release job may therefore find `main` ahead of the commit it tested (`.github/scripts/release_target.sh` holds the cases): behind commits that start no run (docs-only merges, the bot's version commits) it releases from the tip; behind a later code commit it leaves the release to that commit's run, with a warning while that run is alive and a failed job when that run failed or was cancelled. Nothing is released while `main` is red.
1. **Security Scan** — Trivy filesystem scan (HIGH/CRITICAL fail the job).
2. **Code Quality** — Ruff format + lint, `ty check`, OpenAPI schema validation, and the committed `schema.yml` matching the code (structurally, via oasdiff).
3. **Migration Check** — replays a deploy: migrates at the base commit, then runs the head's `makemigrations --check` and `migration_preflight`, then both django-tenants migration paths on top.
4. **Testing** — 4 duration-balanced pytest shards (pytest-split, `.test_durations`), one xdist worker per runner vCPU, each on a copy of a database the shard builds once, against PostgreSQL 18 + Redis + Meilisearch, each uploading raw coverage data.
5. **Testing (multi-tenant lane)** — `tests_mt` serially with the real tenant router and middleware; its three store schemas are migrated at the same time (`mt_store_schemas` in `tests_mt/conftest.py`).
6. **Coverage Gate** — combines the shard data and enforces `fail_under`; also merges the shards' test durations into the `test-durations` artifact (the refresh of `.test_durations`, `docs/testing.md`).
7. **Release** — python-semantic-release (run from the locked dev group) cuts the version, changelog and GitHub release on `main`; its version commit also carries the re-locked `uv.lock` (`build_command` + `assets` in `pyproject.toml`), so `main` never holds a stale lock. The Docker image workflow triggers on the published release. Nothing is built or published to PyPI — this is a deployed application, not a library.

`.github/workflows/api-contract.yml` is separate: it calls the reusable `api-contract-check.yml`, which pairs the PR's documents with the release live in production and in staging (GitHub Deployments, recorded from the pods) and runs `api-contract/api-contract.sh`; the storefront and gateway PR checks call the same workflow. It always reports, so it is a required check; a PR that touches no contract document passes at once.

## Code Style

- **Line length**: 80 characters
- **Formatter/Linter**: Ruff (`target-version = "py315"`)
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
- **Moving code.** A Celery task that changes module keeps its name with
  `name="<the name it has today>"`: queued messages, the beat schedule and
  `DeferredDispatch` rows address it by that string, and
  `tests/unit/core/test_celery_task_names_are_locked.py` fails otherwise. A
  doc, rule or skill that names a moved file changes in the same PR
  (`tests/unit/test_doc_references.py`).
- Semantic release: `feat` → minor, `fix`/`perf`/`refactor` → patch (a `refactor` moves code that runs in production, so it ships and is verified as its own release)

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
