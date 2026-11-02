# GrooveShop Django API

[![CI](https://github.com/vasilistotskas/grooveshop-django-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/vasilistotskas/grooveshop-django-api/actions/workflows/ci.yml)

The headless, multi-tenant e-commerce backend of GrooveShop: a Django 6 and
Django REST Framework API with the store admin, background jobs and real-time
notifications.

GrooveShop runs many stores on one platform. Each store is a PostgreSQL schema
(django-tenants) resolved from the request host. This API is the system of
record: the Nuxt storefront proxies its `/api/...` calls here, the agent gateway
reaches it over REST, and the storefront's TypeScript types and Zod schemas are
generated from the OpenAPI `schema.yml` committed in this repository. See the
[platform architecture](https://docs.grooveshop.space/dev/platform/architecture)
for how the services fit together. The developer docs are behind basic auth.

## Features

- **Catalogue**: products with translations, categories (trees), images, tags,
  reviews, favourites, stock with atomic reservations, EU Omnibus prior-price
  tracking, and product recommendations.
- **Checkout and orders**: carts, guest and signed-in checkout, order and
  payment state machines, transactional email, and turnover and profit reports.
- **Payments**: Viva Wallet (primary), Stripe through dj-stripe, and cash on
  delivery. Credentials live on each store's tenant row.
- **Shipping**: a carrier abstraction with ACS and BoxNow integrations.
- **Marketing**: a rule-based promotion engine with coupons, gift cards with a
  ledger, loyalty points and tiers, a B2B wholesale program with VIES
  validation and group pricing, and Meta Conversions API events.
- **Search**: Meilisearch product and blog search with Greeklish
  transliteration at index time, facets and search analytics.
- **Content**: a blog, merchant-editable storefront layouts and navigation,
  store-editable storefront texts, and the storefront page builder (versioned
  documents and releases).
- **Accounts**: Knox token auth, django-allauth headless (Google, Facebook,
  GitHub and Discord sign-in, MFA, WebAuthn and passkeys), and allauth as the
  OAuth 2.1 / OIDC provider for AI agents that call `/api/v1/agent/*` through
  the agent gateway.
- **Multi-tenancy**: provisioning, plans and billing, per-store feature
  switches and settings, staff roles, and encrypted per-store secrets.
- **Real-time**: WebSocket notifications over Django Channels, authenticated by
  a single-use ticket.
- **Admin**: a Django Unfold store admin and a separate platform admin.
- **Translations**: Greek (default) and English through django-parler; which
  locales a store serves is set per store.

## Tech stack

- Python 3.14, Django 6, Django REST Framework, managed with uv
- ASGI only: Gunicorn with Uvicorn workers in production, Uvicorn locally;
  Django Channels for WebSockets
- PostgreSQL 18 with django-tenants (schema per store)
- Redis (cache, Channels layer, WebSocket tickets)
- Celery with a RabbitMQ broker; django-celery-beat for schedules
- Meilisearch
- drf-spectacular for the OpenAPI schema
- Django Unfold for the admin

## Getting started

Prerequisites: Python 3.14, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
# Backing services: PostgreSQL, Redis, RabbitMQ, Meilisearch, Mailpit,
# pgAdmin and RedisInsight
docker compose -f infra.compose.yml up -d

cp .env.example .env            # then fill in the values
uv sync --locked --all-extras --dev
uv run python manage.py migrate # django-tenants: migrates every schema
```

Every request needs a tenant, resolved from its host. Create the platform row
and a local store:

```bash
uv run python manage.py bootstrap_platform --domain platform.localhost
uv run python manage.py tenant_create --name "Local store" --slug local \
  --schema local --domain localhost --owner-email you@example.com
uv run python manage.py seed_all --schema local   # optional sample data
```

Run the API (HTTP and WebSocket on port 8000), and the Celery worker and beat
when you need background jobs:

```bash
uv run uvicorn asgi:application --reload
uv run celery --app core worker --queues celery,meilisearch --loglevel=info
uv run celery --app core beat --loglevel=info \
  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

`manage.py runserver` is not the dev server: the project is ASGI only.
`docker compose up -d --build` runs the whole stack in containers instead
(`docker-compose.yml` includes `infra.compose.yml` and `app.compose.yml`).

Fill the search indexes for a store with
`uv run python manage.py meilisearch_sync_all_indexes --tenant local`. Every
`meilisearch_*` command takes `--tenant <schema>` or `--all-tenants`; see
[the command reference](docs/search/management-commands.md).

The API is served under `/api/v1/`. Swagger UI is at
`/api/v1/schema/swagger-ui`, ReDoc at `/api/v1/schema/redoc`, and the health
check at `/api/v1/health`.

## Common commands

```bash
uv run pytest                                  # all tests, in parallel
uv run pytest tests/unit/path/to/test_file.py::test_name
uv run pytest tests_mt -n 0                    # multi-tenant lane
uv run ruff check --fix && uv run ruff format  # lint and format
uv run ty check                                # type check
uv run pre-commit run --all-files

uv run python manage.py makemigrations
uv run python manage.py spectacular --color --file schema.yml   # OpenAPI schema
uv run python manage.py makemessages -l el -l en                # translations
```

Tests need PostgreSQL and Redis from `infra.compose.yml`. Read
[docs/testing.md](docs/testing.md) before writing a test that commits or touches
settings, seeds or the cache.

## Project layout

Every Django app sits at the repository root; there is no `src/` directory.
`settings.py`, `asgi/` and `manage.py` are at the root too, and `core/urls.py`
mounts every app under `api/v1/`.

| Area | Apps |
|---|---|
| Shared infrastructure | `core`, `devtools`, `admin`, `emails` |
| Multi-tenancy | `tenant` |
| Catalogue | `product`, `tag`, `recommendation` |
| Checkout and orders | `cart`, `order`, `pay_way`, `vat`, `country`, `region` |
| Shipping | `shipping`, `shipping_acs`, `shipping_boxnow` |
| Marketing | `promotion`, `giftcard`, `loyalty`, `b2b`, `meta_capi` |
| Content | `blog`, `page_config`, `texts`, `builder`, `contact` |
| Search | `search`, `meili` |
| Accounts and real-time | `user`, `notification`, `agent` |

[CLAUDE.md](CLAUDE.md) describes the base classes, patterns and conventions in
detail.

## Deployment and release

- CI (`.github/workflows/ci.yml`) runs a Trivy scan, Ruff, `ty`, a check that
  the committed `schema.yml` matches the code, a migration replay, four test
  shards, the multi-tenant lane and a coverage gate.
- A separate API contract workflow fails a pull request that breaks the
  schema's clients unless it carries the `api-breaking-change` label.
- On `main`, python-semantic-release cuts the version, changelog and GitHub
  release from Conventional Commits (`feat` is a minor, `fix` and `perf` a
  patch).
- A published release builds the Docker image and pushes it to Docker Hub
  (`gro0ve/grooveshop-django-api`) and GHCR.
- Deployment is GitOps: the image tag is pinned in
  grooveshop-infrastructure (private) and Argo CD syncs it to the K3s
  cluster. A PreSync hook runs the migrations
  before the new pods roll, so a migration may only add. Removing or renaming
  takes two releases ([docs/migrations.md](docs/migrations.md)).

## Documentation

- [`docs/`](docs/): the deep reference for this service (orders, emails,
  search, pricing, the page builder, migrations, testing and more). It is
  published on the docs site under
  [/dev/django/reference](https://docs.grooveshop.space/dev/django/reference/order-system).
- [docs.grooveshop.space](https://docs.grooveshop.space): the public admin guide
  for store owners (Greek and English), and the developer docs under `/dev`
  (basic auth).
- Live demo store: [demo.grooveshop.space](https://demo.grooveshop.space)
- [SECURITY.md](SECURITY.md) for reporting a vulnerability.

## Related repositories

- [grooveshop-django-api](https://github.com/vasilistotskas/grooveshop-django-api): the multi-tenant REST API, admin and background jobs (Django).
- [grooveshop-storefront-ui-node-nuxt](https://github.com/vasilistotskas/grooveshop-storefront-ui-node-nuxt): the server-rendered storefront and API proxy (Nuxt).
- [grooveshop-media-stream](https://github.com/vasilistotskas/grooveshop-media-stream): image resizing, conversion and caching for the storefront (NestJS).
- [grooveshop-agent-gateway](https://github.com/vasilistotskas/grooveshop-agent-gateway): MCP, UCP and ACP endpoints for AI shopping agents (Go).
- [grooveshop-infrastructure](https://github.com/vasilistotskas/grooveshop-infrastructure): Kubernetes manifests deployed by Argo CD (K3s; private).
- [grooveshop-docs](https://github.com/vasilistotskas/grooveshop-docs): the documentation site at docs.grooveshop.space (VitePress).

## Licence

See [LICENSE.md](LICENSE.md).
