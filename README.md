[![Coverage Status](https://coveralls.io/repos/github/vasilistotskas/grooveshop-django-api/badge.svg?branch=main)](https://coveralls.io/github/vasilistotskas/grooveshop-django-api?branch=main)

# Grooveshop Django API

## Overview

A headless e-commerce API built with Django 6.0 and Django REST Framework. Supports both
WSGI (Gunicorn) and ASGI (Daphne/Uvicorn) with real-time WebSocket notifications via
Django Channels. Uses Knox + Django Allauth for authentication (token API + social/MFA),
Celery with RabbitMQ broker for background tasks, PostgreSQL 17 for data storage, Redis
for caching and Channels layer, and Meilisearch for federated search. Features include
multi-language support (Greek, English, German), Stripe payments via dj-stripe,
comprehensive test coverage, and a Django Unfold admin panel.

## Project Structure

All Django apps live at the project root (flat structure, no `src/` directory):

- **core/** — Shared infrastructure: base views, serializers, permissions, middleware, filters, caching, Celery config, URL routing
- **user/** — User accounts, authentication, and profile management
- **product/** — Product catalog, categories, reviews, favourites, images, and stock management
- **order/** — Order processing and management
- **cart/** — Shopping cart functionalities
- **blog/** — Blog posts, categories, comments, authors, and tags
- **search/** — Meilisearch integration with federated search, Greeklish expansion, and analytics
- **meili/** — Meilisearch model definitions and indexing via `IndexMixin` with `MeiliMeta` config
- **notification/** — Real-time notifications via WebSocket (Django Channels)
- **loyalty/** — Points, XP, tiers, and rewards system
- **country/** — Country-specific configurations
- **region/** — Regional data and settings
- **vat/** — VAT calculation and management
- **pay_way/** — Payment method configurations
- **tag/** — Tagging system for products and blog posts
- **contact/** — Contact form and communication
- **admin/** — Custom admin app with Django Unfold dashboard
- **devtools/** — `CustomDjangoModelFactory` base class for all test factories

## Features

- **Authentication and User Management**: Knox token auth for API, Django Allauth for account management with social providers (Google, Facebook, GitHub, Discord), MFA/WebAuthn/Passkeys support
- **Multi-Language Support**: django-parler translations for Greek (default), English, and German
- **Advanced Search and Filtering**: Meilisearch-powered federated search with Greeklish support, instant search, and analytics tracking
- **Payments**: Stripe integration via dj-stripe
- **Loyalty System**: Points, XP, tiers, and rewards
- **Task Scheduling**: Celery with RabbitMQ for background task management (stock cleanup, cart expiry, notifications, Meilisearch sync)
- **Real-time Notifications**: WebSocket via Django Channels with per-user and admin groups
- **Performance Optimization**: Redis caching, optimized querysets with `for_list()`/`for_detail()` patterns
- **Testing**: Comprehensive unit and integration tests with pytest-xdist parallel execution
- **Admin Panel**: Django Unfold admin panel with custom dashboard
- **API Documentation**: OpenAPI 3.0 via drf-spectacular (Swagger UI and Redoc)
- **Containerization**: Docker Compose for full-stack deployment

## Technologies

- **Frameworks**: Django 6.0, Django REST Framework 3.16
- **Authentication**: Django REST Knox (API tokens), Django Allauth (accounts, social, MFA)
- **Database**: PostgreSQL 17
- **Cache / Channels**: Redis
- **Task Management**: Celery
- **Message Broker**: RabbitMQ
- **Search**: Meilisearch v1.42.1
- **Payments**: Stripe (dj-stripe)
- **Server**: Uvicorn (ASGI), Gunicorn (WSGI), Daphne (Channels)
- **Containerization**: Docker
- **Package Management**: uv

## Setup

### Prerequisites

- Python 3.14 or higher
- Django 6.0 or higher
- PostgreSQL 17
- Redis
- RabbitMQ
- Meilisearch v1.42.1 or higher

### Meilisearch Setup

#### Installation

**Using Docker:**
```bash
docker run -d \
  --name meilisearch \
  -p 7700:7700 \
  -e MEILI_MASTER_KEY=YOUR_MASTER_KEY \
  -v $(pwd)/meili_data:/meili_data \
  getmeili/meilisearch:v1.42.1
```

**Using Docker Compose:**
```yaml
services:
  meilisearch:
    image: getmeili/meilisearch:v1.42.1
    ports:
      - "7700:7700"
    environment:
      - MEILI_MASTER_KEY=YOUR_MASTER_KEY
    volumes:
      - ./meili_data:/meili_data
```

#### Configuration

Add Meilisearch settings to your `.env` file:
```env
MEILISEARCH_HOST=http://localhost:7700
MEILISEARCH_MASTER_KEY=YOUR_MASTER_KEY
```

#### Enable Experimental Features

Enable the CONTAINS operator for substring matching:
```bash
python manage.py meilisearch_enable_experimental --feature containsFilter
```

#### Configure Index Settings

Update index settings for optimal performance:
```bash
# ProductTranslation index
python manage.py meilisearch_update_index_settings \
    --index ProductTranslation \
    --max-total-hits 50000 \
    --search-cutoff-ms 1500 \
    --max-values-per-facet 100

# BlogPostTranslation index
python manage.py meilisearch_update_index_settings \
    --index BlogPostTranslation \
    --max-total-hits 50000 \
    --search-cutoff-ms 1500 \
    --max-values-per-facet 100
```

#### Configure Ranking Rules

Set up custom ranking rules for e-commerce:
```bash
# Prioritize in-stock products and discounts
python manage.py meilisearch_update_ranking \
    --index ProductTranslation \
    --rules "words,typo,proximity,attribute,sort,stock:desc,discount_percent:desc,exactness"

# Prioritize popular blog posts
python manage.py meilisearch_update_ranking \
    --index BlogPostTranslation \
    --rules "words,typo,proximity,attribute,sort,view_count:desc,exactness"
```

#### Sync Data to Meilisearch

```bash
# Sync all indexes
python manage.py meilisearch_sync_all_indexes

# Or sync specific indexes
python manage.py meilisearch_sync_index --model ProductTranslation
python manage.py meilisearch_sync_index --model BlogPostTranslation
```

#### Test Federated Search

```bash
python manage.py meilisearch_test_federated \
    --query "laptop" \
    --language-code en \
    --limit 20
```

### OpenAPI Schema Generation

Generate TypeScript types and Zod schemas for the frontend:

```bash
# Generate OpenAPI schema
uv run python manage.py spectacular --color --file schema.yml

# Frontend will use this schema to generate types
# See grooveshop-storefront-ui-node-nuxt/README.md for frontend setup
```

## Common Commands

```bash
# Install dependencies (uses uv, not pip)
uv sync --locked --all-extras --dev

# Run all tests (parallel by default)
uv run pytest

# Run a single test file
uv run pytest tests/unit/path/to/test_file.py

# Run tests with coverage (sequential)
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
uv run python manage.py createsuperuser
uv run python manage.py seed_all

# Generate OpenAPI schema (used by Nuxt frontend for types)
uv run python manage.py spectacular --color --file schema.yml

# Meilisearch index management
uv run python manage.py meilisearch_sync_all_indexes
uv run python manage.py meilisearch_sync_index --model ProductTranslation

# Celery (local development)
celery -A core beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
celery -A core worker -E -l info --pool=solo
celery -A core flower --broker=amqp://guest:guest@localhost:5672// --broker_api=http://guest:guest@localhost:15672/api// --port=5555

# Docker (full stack)
docker compose up -d --build
```

## Meilisearch Management

### Index Management
```bash
uv run python manage.py meilisearch_sync_all_indexes
uv run python manage.py meilisearch_sync_index --model ProductTranslation
uv run python manage.py meilisearch_clear_index --index ProductTranslation
uv run python manage.py meilisearch_drop --index ProductTranslation
uv run python manage.py meilisearch_inspect_index --index ProductTranslation
```

### Configuration
```bash
uv run python manage.py meilisearch_enable_experimental --feature containsFilter
uv run python manage.py meilisearch_update_index_settings --index ProductTranslation --max-total-hits 50000 --search-cutoff-ms 1500
uv run python manage.py meilisearch_update_ranking --index ProductTranslation --rules "words,typo,proximity,attribute,sort,stock:desc,discount_percent:desc,exactness"
```

### Testing and Analytics
```bash
uv run python manage.py meilisearch_test_federated --query "laptop" --language-code en --limit 20
uv run python manage.py meilisearch_export_analytics --start-date 2024-01-01 --end-date 2024-12-31 --output analytics.json
```

For detailed documentation, see:
- [Search API Documentation](docs/api/search.md)
- [CONTAINS Operator Guide](docs/search/contains-operator.md)
- [Management Commands Reference](docs/search/management-commands.md)

## License

This project is open-sourced under the MIT License. See the [LICENSE](LICENSE.md) file for more details.
