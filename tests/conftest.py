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
    },
}


# Route django-extra-settings through a DummyCache so every ``Setting.get``
# falls through to the DB. The package's ``post_save`` hook updates the
# cache eagerly, but under EAGER + the on_commit-immediate fixture the
# cache state can drift in ways that are hard to reproduce (a signal-fired
# task body can clear/repopulate it mid-test). Using DummyCache means
# tests always read the just-written DB row, eliminating an entire class
# of "setting was set but reads back empty" flakes (e.g. mydata tests'
# ``INVOICE_SELLER_VAT_ID``). Cost is one extra DB read per ``Setting.get``,
# which is dwarfed by the EAGER signal cascades the same tests trigger.
#
# Patch the package's ``_get_cache`` directly rather than registering a
# new "extra_settings" alias in CACHES — Django's ``caches`` connection
# handler materialises its settings dict lazily via a cached_property at
# app-load time, so adding new aliases here would require resetting that
# registry, which in turn forces every cache lookup (including the one
# in ``cache._cache.get_client`` patched by Channels middleware tests)
# to rebuild against the test settings instead of the production
# Redis-backed registry the tests assume.
def _dummy_extra_settings_cache():
    from django.core.cache.backends.dummy import DummyCache as _DummyCache

    return _DummyCache("extra-settings-dummy", {})


import extra_settings.cache as _extra_settings_cache  # noqa: E402

_extra_settings_cache._get_cache = _dummy_extra_settings_cache

settings.DATABASES["default"]["ATOMIC_REQUESTS"] = False
settings.DATABASES["default"]["AUTOCOMMIT"] = True
settings.DATABASES["default"]["CONN_MAX_AGE"] = 0
# Drop the production statement_timeout / idle-in-transaction guards for
# the test suite. Under heavy parallel xdist load (-n auto), inline EAGER
# task bodies fired by signal handlers can hold connections long enough
# for the 30s production timeout to fire, producing
# ``OperationalError('canceling statement due to statement timeout')``
# flakes that have nothing to do with the test under inspection.
# Tests still time out at the pytest level via ``timeout = 600`` in
# pyproject.toml, so removing the per-statement guard does not let a
# real hang slip through silently.
_test_db_options = dict(settings.DATABASES["default"].get("OPTIONS", {}))
_test_db_options["options"] = (
    "-c statement_timeout=0 -c idle_in_transaction_session_timeout=0"
)
# Disable the psycopg connection pool for the test suite. Pooling
# extends connection lifetimes per-process so that ``conn.close()`` on
# a Django wrapper hands the underlying socket back to the pool rather
# than terminating the Postgres session. That bites on
# TransactionTestCase teardown: pytest-django's ``flush`` step issues
# TRUNCATE against every table, which blocks behind any other session
# still holding row-level locks (e.g. an async test's lingering
# ``database_sync_to_async`` connection). When the truncate stalls or
# fails, the next test in the same worker observes leaked rows
# (e.g. ``InvoiceCounter`` for year 2026 already present, breaking
# ``test_allocate_creates_counter_on_first_call``). With pooling off,
# ``conn.close()`` actually terminates the session, freeing locks
# immediately.
_test_db_options.pop("pool", None)
settings.DATABASES["default"]["OPTIONS"] = _test_db_options

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
def _run_transaction_on_commit_immediately(request, monkeypatch):
    """Execute ``transaction.on_commit`` callbacks synchronously in tests.

    Signal handlers and Celery dispatches across the codebase wrap work in
    ``transaction.on_commit`` so that workers see committed rows (production
    correctness). Django's ``TestCase`` wraps every test in a savepoint that
    is rolled back at the end — the outer transaction never commits, so the
    callbacks would never run, and tests asserting on dispatch behaviour
    would see empty mocks.

    This fixture replaces ``transaction.on_commit`` with a direct call for
    the duration of each test. Tests that explicitly ``@patch`` it to verify
    deferral still work because the per-test patch takes precedence.

    Skipped when the test explicitly uses ``transaction=True`` django_db
    mode (TransactionTestCase), since those commit normally.
    """
    marker = request.node.get_closest_marker("django_db")
    if marker and marker.kwargs.get("transaction", False):
        return

    from django.db import transaction as _tx

    def _immediate(func, using=None, robust=False):
        # Swallow callback exceptions. Under CELERY_TASK_ALWAYS_EAGER, a
        # ``task.delay()`` inside an on_commit callback actually executes
        # the task body — some tasks (e.g. PDF invoicing via WeasyPrint)
        # need native libs that aren't available in the test environment
        # and raise Celery Retry exceptions. In production, ``.delay()``
        # only enqueues; the task body runs in a worker. Swallowing here
        # makes the fixture behave like ``on_commit(..., robust=True)``.
        try:
            func()
        except Exception:  # pragma: no cover - swallow like robust=True
            pass

    monkeypatch.setattr(_tx, "on_commit", _immediate)


@pytest.fixture(autouse=True)
def reset_db_queries():
    reset_queries()
    yield
    reset_queries()


@pytest.fixture(autouse=True)
def _close_db_connections_after_test(request):
    """Release idle DB connections at the end of every test.

    Prevents two failure modes that surface under parallel xdist:

    1. ``OperationalError('database "test_postgres_gwN" is being
       accessed by other users')`` during ``TransactionTestCase``
       teardown — async helpers, Channels async-to-sync wrappers, and
       Celery EAGER task bodies leave per-thread connections behind that
       the test runner's ``flush`` step can't preempt.
    2. Pool exhaustion mid-suite — psycopg's pool caps connections per
       process, and EAGER signal cascades open many short-lived ones
       that linger on the pool's free-list well after the test ends.

    Closing every non-atomic connection at the end of each test bounds
    both. The pool reopens connections lazily on next use, so this is
    cheap.
    """
    yield
    for conn in connections.all():
        if conn.connection is not None and not conn.in_atomic_block:
            try:
                conn.close()
            except Exception:  # pragma: no cover - close is best-effort
                pass


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
