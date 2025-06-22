import pytest
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection, reset_queries

settings.PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

settings.MEILI_OFFLINE = True

settings.DATABASES["default"]["ATOMIC_REQUESTS"] = False
settings.DATABASES["default"]["AUTOCOMMIT"] = True

settings.CELERY_TASK_ALWAYS_EAGER = True
settings.CELERY_TASK_EAGER_PROPAGATES = True
settings.DEBUG = False


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
def _django_clear_site_cache():
    Site.objects.clear_cache()


@pytest.fixture
def debug_query_count():
    connection.force_debug_cursor = True
    yield
    connection.force_debug_cursor = False


@pytest.fixture(autouse=True)
def _django_clear_cache():
    cache.clear()


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
