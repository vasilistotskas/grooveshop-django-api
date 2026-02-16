# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GrooveShop Django API — a headless e-commerce API built with Django 5.2 and Django REST Framework. Supports both WSGI (Gunicorn) and ASGI (Daphne/Uvicorn) with WebSocket notifications via Django Channels. Uses PostgreSQL 17, Redis, Celery (RabbitMQ broker), and Meilisearch. Python 3.14.2, managed with uv.

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

# Run all pre-commit hooks
uv run pre-commit run --all-files

# Django management
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py runserver
uv run python manage.py seed_all

# Generate OpenAPI schema (used by Nuxt frontend for types)
uv run python manage.py spectacular --color --file schema.yml

# Meilisearch index management
uv run python manage.py meilisearch_sync_all_indexes
uv run python manage.py meilisearch_sync_index --model ProductTranslation

# Docker (full stack)
docker compose up -d --build
```

## Architecture

### Settings

Single `settings.py` at the project root (not inside any app). Uses `SYSTEM_ENV` env var (`dev`, `production`, `ci`) for environment-specific behavior. All config via `.env` file loaded with python-dotenv. The `DJANGO_SETTINGS_MODULE` is just `"settings"`.

### Django Apps

All apps live at the project root (flat structure, no `src/` directory):

- **core/** — Shared infrastructure: base views, serializers, permissions, middleware, filters, caching, Celery config, URL routing. The `core/urls.py` is the root URL conf that mounts all other apps under `api/v1/`.
- **product/**, **order/**, **cart/**, **blog/**, **user/** — Main domain apps
- **search/** — Meilisearch integration with federated search, Greeklish expansion, and analytics
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
- `SeoModel` — `seo_title`, `seo_description`, `seo_keywords`
- `SortableModel` — `sort_order` with atomic `move_up()`/`move_down()` using `select_for_update()`
- `PublishableModel` — `is_published`/`published_at` with `PublishableManager.published()` queryset
- `MetaDataModel` — `metadata`/`private_metadata` JSONFields with GinIndex
- `SoftDeleteModel` — `is_deleted`/`deleted_at` with `SoftDeleteManager` (`.all_with_deleted()`, `.deleted_only()`, `.restore()`, `.hard_delete()`)

Domain models compose multiple mixins, e.g. `Product(SoftDeleteModel, TranslatableModel, TimeStampMixinModel, SeoModel, UUIDModel, MetaDataModel, TaggedModel)`.

**Base ViewSet** (`core/api/views.py: BaseModelViewSet`):
- Combines `RequestResponseSerializerMixin`, `TranslationsModelViewSet`, `PaginationModelViewSet`
- Uses `serializers_config` dict mapping actions to `ActionConfig` objects (separate request/response serializers per action)
- Supports three pagination strategies via `?pagination_type=` query param: `pageNumber` (default), `cursor`, `limitOffset`
- Handles multipart translation data via `TranslationsProcessingMixin`
- Atomic transactions on create/update

**Optimized Managers** (`core/managers/`):
- `OptimizedManager`/`OptimizedQuerySet` — Override `for_list()` and `for_detail()` for select_related/prefetch_related
- `TranslatableOptimizedManager`/`TranslatableOptimizedQuerySet` — Adds `with_translations()` for parler
- `TreeTranslatableManager`/`TreeTranslatableQuerySet` — For MPTT + Parler models (categories)
- ViewSets call `Model.objects.for_list()` or `.for_detail()` based on action

**Composable FilterSets** (`core/filters/`):
- Mixin-based: `TimeStampFilterMixin`, `PublishableFilterMixin`, `SoftDeleteFilterMixin`, `MetaDataFilterMixin`, `UUIDFilterMixin`, `SortableFilterMixin`
- CamelCase variants: `CamelCaseFilterMixin` auto-converts query param names
- Pre-built: `BaseFullFilterSet` (all mixins), `CamelCasePublishableTimeStampFilterSet`, etc.

**Pagination** (`core/pagination/`): `PageNumberPaginator`, `CursorPaginator`, `LimitOffsetPaginator` — all return consistent envelope: `{links, count, total_pages, page_size, page_total_results, page, results}`

**Serializer utilities** (`core/utils/serializers.py`):
- `ActionConfig` dataclass — per-action request/response serializer + OpenAPI metadata
- `crud_config()` — shorthand for standard CRUD serializer mapping
- `create_schema_view_config()` — auto-generates `@extend_schema` decorators from model verbose names

**Custom fields**: `ImageAndSvgField` (images + SVG), `MeasurementField` (physical measurements with unit conversion)

**Permissions** (`core/api/permissions.py`): `IsOwnerOrAdmin`, `IsOwnerOrAdminOrGuest` — checks `user`, `owner`, or `created_by` fields

### API Conventions

- All API endpoints are under `api/v1/` with DRF ViewSets
- Request/response bodies use **camelCase** (auto-converted from snake_case via `djangorestframework-camel-case`)
- Authentication: Knox token auth (`Bearer` prefix) + Django session auth
- Default pagination: 12 items per page, max 100
- OpenAPI docs at `/api/v1/schema/swagger-ui` and `/api/v1/schema/redoc`
- Health check at `/api/v1/health` (checks DB, Redis, Celery)
- Serializer tiering: separate List/Detail/Write serializers per model

### Domain Patterns

- **Translations**: django-parler `TranslatableModel` on Product, BlogPost, Category, LoyaltyTier, etc. Languages: el (default), en, de. Factories create translations for all languages.
- **Audit history**: django-simple-history on models
- **Tree structures**: django-mptt `TreeForeignKey` for ProductCategory, BlogCategory
- **Monetary fields**: django-money `MoneyField` for prices, totals. Default currency: EUR
- **Stock management**: `StockManager` with atomic `reserve_stock()`/`release_stock()` using `select_for_update()`. `StockReservation` model with TTL (default 15 min). `StockLog` for audit trail.
- **Computed properties**: Models check `__dict__` for annotation values before falling back to DB queries (e.g. `likes_count`, `review_average`)
- **Meilisearch indexing**: Models inherit `IndexMixin` with `MeiliMeta` class defining filterable/searchable/sortable fields, ranking rules, synonyms, typo tolerance. `meili_filter()` controls indexing eligibility. `get_additional_meili_fields()` adds computed fields.

### WebSocket / Real-time

ASGI routing in `asgi/__init__.py` with Channels `ProtocolTypeRouter`:
- HTTP: Django ASGI with CORS handler
- WebSocket: `ws/notifications/` → `NotificationConsumer`
- Auth via `TokenAuthMiddleware` (Knox token in query params)
- Groups: `user_{id}` per-user, `admins` for staff

### Celery

App configured in `core/celery.py`. Base task class: `MonitoredTask` (logs success/failure). Tasks in `core/tasks.py` include system health monitoring, DB backup, cache clearing, session cleanup, Meilisearch sync, abandoned cart cleanup, loyalty points expiration, inactive user notifications. All tasks use auto-retry with exponential backoff (max 5 retries).

### Factory / Test Data Pattern

All factories extend `CustomDjangoModelFactory` from `devtools/factories.py`. Key features:
- `auto_translations` flag for parler models
- `unique_model_fields` list for collision-free field generation
- `@factory.post_generation` for related objects (images, reviews, translations, cart items)
- `get_or_create_instance()` helper in `core/helpers/factory.py` for dynamic model/factory resolution
- `UserNameGenerator` in `core/generators.py` generates `{Adjective}{Noun}#{hash}` usernames from email

### Test Configuration

Tests in `tests/` with `unit/` and `integration/` subdirectories. Key `conftest.py` settings:
- MD5 password hasher (faster than default)
- `DISABLE_CACHE = True`, `MEILISEARCH["OFFLINE"] = True`
- `CELERY_TASK_ALWAYS_EAGER = True` (synchronous execution)
- Auto-fixtures: cache clearing, DB query reset, site cache clear, connection cleanup for xdist
- `requires_meilisearch` skip marker for tests needing live Meilisearch
- `count_queries` fixture and `QueryCountAssertionMixin` for N+1 detection
- Coverage minimum: 50% (`fail_under = 50`), timeout: 600s

### Infrastructure (Docker)

- `infra.compose.yml` — PostgreSQL 17, Redis, RabbitMQ, Meilisearch v1.22.3, pgAdmin, RedisInsight
- `app.compose.yml` — backend-init (migrations), backend, celery_worker, celery_beat, celery_flower
- `docker-compose.yml` — Combines both on `grooveshop-backbone` bridge network
- `Dockerfile` — Multi-stage Alpine build (uv → tailwind CSS → Python deps → production)
- `dev.Dockerfile` — Debian slim with full dev environment

### CI/CD

GitHub Actions (`.github/workflows/ci.yml`): 3-stage pipeline:
1. **Quality** — Ruff format check
2. **Testing** — PostgreSQL 17 + Redis + Meilisearch services, migrations, pytest with coverage (15 min timeout)
3. **Release** — python-semantic-release → TestPyPI → PyPI (main branch only)

## Code Style

- **Line length**: 80 characters
- **Formatter/Linter**: Ruff (targets Python 3.13)
- **Max function args**: 6 (pylint rule via ruff)
- Migrations are excluded from linting (`**/migrations/**`)
- Semantic release: `feat` → minor, `fix`/`perf` → patch
