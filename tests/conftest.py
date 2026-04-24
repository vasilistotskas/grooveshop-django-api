import os

import pytest
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection, connections, reset_queries
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

# Hypothesis profiles for different environments
hypothesis_settings.register_profile(
    "ci",
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
)
hypothesis_settings.register_profile(
    "dev",
    max_examples=10,
    deadline=None,
)
hypothesis_settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
)
hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

settings.PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

settings.DISABLE_CACHE = True
settings.MEILISEARCH["OFFLINE"] = True
settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Strip unnecessary middleware for test performance
settings.MIDDLEWARE = [
    m
    for m in settings.MIDDLEWARE
    if m
    not in {
        "django.middleware.gzip.GZipMiddleware",
        "core.middleware.stripe_webhook.StripeWebhookDebugMiddleware",
        "core.middleware.asgi_compat.ASGICompatMiddleware",
    }
]

# Use process-local in-memory cache instead of Redis for test isolation.
# With pytest-xdist (-n auto), each worker is a separate process with its own
# LocMemCache. This prevents cache.clear() in one worker from doing FLUSHDB
# on shared Redis and wiping keys that other workers depend on (e.g.
# django-extra-settings cached values).
settings.CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

settings.DATABASES["default"]["ATOMIC_REQUESTS"] = False
settings.DATABASES["default"]["AUTOCOMMIT"] = True
settings.DATABASES["default"]["CONN_MAX_AGE"] = 0

# Disable multi-tenancy for tests — all tables in public schema.
# Multi-tenancy schema isolation is tested separately; unit/integration
# tests don't need per-schema separation.
settings.DATABASE_ROUTERS = []

# Remove TenantMainMiddleware so tests don't need a TenantDomain for "testserver"
settings.MIDDLEWARE = [
    m
    for m in settings.MIDDLEWARE
    if m != "django_tenants.middleware.main.TenantMainMiddleware"
]

# Use ROOT_URLCONF directly (not PUBLIC_SCHEMA_URLCONF) for tests
if hasattr(settings, "PUBLIC_SCHEMA_URLCONF"):
    del settings.PUBLIC_SCHEMA_URLCONF

settings.CELERY_TASK_ALWAYS_EAGER = True
settings.CELERY_TASK_EAGER_PROPAGATES = True
settings.DEBUG = False


def is_meilisearch_available():
    """Check if Meilisearch is available for testing."""
    try:
        import meilisearch

        host = os.environ.get("MEILI_HTTP_ADDR", "http://localhost:7700")
        key = os.environ.get("MEILI_MASTER_KEY", "")
        client = meilisearch.Client(host, key)
        client.health()
        return True
    except Exception:
        return False


# Check Meilisearch availability once at module load
MEILISEARCH_AVAILABLE = is_meilisearch_available()

# Skip marker for tests requiring Meilisearch
requires_meilisearch = pytest.mark.skipif(
    not MEILISEARCH_AVAILABLE,
    reason="Meilisearch is not available",
)


@pytest.fixture(autouse=True)
def clear_caches():
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def reset_db_queries():
    reset_queries()
    yield
    reset_queries()


@pytest.fixture(autouse=True)
def _close_db_connections_after_test(request):
    """Close database connections after TransactionTestCase-style tests.

    Prevents 'database is being accessed by other users' errors during
    TransactionTestCase teardown in parallel execution (-n auto).
    Stale connections from async tests or channels can keep the database
    locked, causing table truncation to fail and data to leak between tests.
    """
    yield
    is_transaction_test = False
    marker = request.node.get_closest_marker("django_db")
    if marker and marker.kwargs.get("transaction", False):
        is_transaction_test = True
    elif hasattr(request, "cls") and request.cls:
        from django.test import TransactionTestCase as DjangoTransactionTestCase
        from django.test import TestCase as DjangoTestCase

        # Only target actual TransactionTestCase, not TestCase
        # (TestCase subclasses TransactionTestCase but uses different isolation)
        if issubclass(
            request.cls, DjangoTransactionTestCase
        ) and not issubclass(request.cls, DjangoTestCase):
            is_transaction_test = True

    if is_transaction_test:
        for conn in connections.all():
            if conn.connection is not None and not conn.in_atomic_block:
                conn.close()


@pytest.fixture(scope="session", autouse=True)
def close_db_connections_on_teardown(request):
    """Close all database connections at the end of the test session to prevent teardown warnings."""
    yield

    def close_connections():
        for conn in connections.all():
            conn.close()

    request.addfinalizer(close_connections)


@pytest.fixture(autouse=True)
def _django_clear_site_cache(request):
    """Clear Site cache if DB access is allowed."""
    if request.node.get_closest_marker("django_db"):
        Site.objects.clear_cache()


@pytest.fixture
def debug_query_count():
    connection.force_debug_cursor = True
    yield
    connection.force_debug_cursor = False


@pytest.fixture(autouse=True)
def _django_clear_cache(request):
    """Clear default cache before tests that use the DB.

    Safe in parallel execution because tests use LocMemCache (process-local).
    """
    if request.node.get_closest_marker("django_db"):
        try:
            cache.clear()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reseed_extra_settings(request):
    """Ensure ``EXTRA_SETTINGS_DEFAULTS`` rows exist before every DB test.

    ``django-extra-settings`` seeds its rows via a ``post_migrate`` signal
    that only fires at DB creation. Tests marked
    ``@pytest.mark.django_db(transaction=True)`` (e.g. concurrent stock
    tests) flush every table on teardown, wiping the ``Setting`` rows.
    A subsequent test calling ``Setting.get("STOCK_RESERVATION_TTL_MINUTES")``
    would hit an empty DB and fall back to the code-level default
    (``StockManager.RESERVATION_TTL_MINUTES_DEFAULT = 15``) instead of
    the configured 30, producing intermittent assertion failures in
    ``tests/integration/order/test_stock_reservation_ttl.py``.

    ``set_defaults_from_settings`` calls ``get_or_create`` per entry —
    cheap no-op when rows are intact, restorative when they are not.
    """
    if request.node.get_closest_marker("django_db"):
        try:
            from extra_settings.models import Setting

            Setting.set_defaults_from_settings()
        except Exception:
            pass


@pytest.fixture
def count_queries():
    class QueryCounter:
        def __init__(self, max_queries=None):
            self.max_queries = max_queries
            self.query_count = 0

        def __enter__(self):
            connection.force_debug_cursor = True
            reset_queries()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.query_count = len(connection.queries)
            if (
                self.max_queries is not None
                and self.query_count > self.max_queries
            ):
                pytest.fail(
                    f"Too many queries: {self.query_count} > {self.max_queries}"
                )
            connection.force_debug_cursor = False

    return QueryCounter


class QueryCountAssertionMixin:
    def assertMaxQueries(self, num, func=None, *args, **kwargs):
        conn = connection
        old_debug_cursor = conn.force_debug_cursor
        conn.force_debug_cursor = True

        try:
            reset_queries()
            func(*args, **kwargs) if func else None
            queries = len(conn.queries)
            assert queries <= num, (
                f"Expected a maximum of {num} queries, but {queries} were performed"
            )
        finally:
            conn.force_debug_cursor = old_debug_cursor

    def assertNumQueries(self, num, func=None, *args, **kwargs):
        conn = connection
        old_debug_cursor = conn.force_debug_cursor
        conn.force_debug_cursor = True

        try:
            reset_queries()
            func(*args, **kwargs) if func else None
            queries = len(conn.queries)
            assert queries == num, (
                f"Expected exactly {num} queries, but {queries} were performed"
            )
        finally:
            conn.force_debug_cursor = old_debug_cursor


pytest.QueryCountAssertionMixin = QueryCountAssertionMixin
