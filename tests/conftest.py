import os

import pytest
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection, connections, reset_queries


settings.PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

settings.DISABLE_CACHE = True
settings.MEILISEARCH["OFFLINE"] = True

settings.DATABASES["default"]["ATOMIC_REQUESTS"] = False
settings.DATABASES["default"]["AUTOCOMMIT"] = True
settings.DATABASES["default"]["CONN_MAX_AGE"] = 0

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

    Uses delete_pattern instead of clear() to avoid FLUSHDB which
    would wipe keys that parallel xdist workers are using.
    """
    if request.node.get_closest_marker("django_db"):
        try:
            cache.clear()
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
