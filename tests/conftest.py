import os

import pytest
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import caches
from django.db import connection, connections, reset_queries
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from redis.exceptions import ConnectionError as RedisConnectionError

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

# Never talk to a real Redis channel layer in tests.
#
# Any code path that emits a websocket notification calls
# ``async_to_sync(channel_layer.group_send)`` — e.g.
# ``notification.tasks.send_notification_task``, reached indirectly by
# order/loyalty flows. With the production RedisChannelLayer that is a
# live async Redis round-trip per test, and under ``-n auto`` every
# worker hammers the same local instance at once. Connections get reset
# mid-command and the test dies with
# ``ConnectionResetError: [WinError 10054]`` wrapped in
# ``redis.exceptions.ConnectionError`` — a failure with nothing to do
# with the behaviour under test, in a DIFFERENT test on each run.
#
# Observed on
# ``test_idempotency_guard_prevents_double_award``: 2 failures in 3
# full-suite runs, always green in isolation. InMemoryChannelLayer keeps
# group_send working (so the calls are still exercised) without leaving
# the process.
settings.CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}

# ``@requires_meilisearch`` tests talk to a REAL engine when one is
# reachable, and under ``-n auto`` every worker queries the same single
# instance at once. The repo's .env pins MEILI_TIMEOUT=10, which is a
# sensible production value but too tight for that burst: the suite
# failed with "Read timed out. (read timeout=10)" on
# /indexes/BlogPostTranslation/search purely from contention, with the
# same tests green at -n0. Raise it for tests only — the assertions are
# about search RESULTS, never latency, so a longer ceiling weakens
# nothing and removes a whole class of parallel-only failures.
MEILI_TEST_TIMEOUT = max(int(settings.MEILISEARCH.get("TIMEOUT") or 0), 60)
settings.MEILISEARCH["TIMEOUT"] = MEILI_TEST_TIMEOUT
settings.SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Deterministic Stripe identity for provider construction: unit tests run
# in the public schema, where stripe_credentials() falls back to the
# platform settings keys — make sure one exists even when the CI env
# defines no STRIPE_TEST_SECRET_KEY (all outbound calls are mocked).
settings.STRIPE_LIVE_MODE = False
settings.STRIPE_TEST_SECRET_KEY = (
    getattr(settings, "STRIPE_TEST_SECRET_KEY", "") or "sk_test_dummy"
)

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

# The ``default`` cache proxy materialised as the production RedisCache at
# app-load, before this module runs. We intentionally do NOT reset
# ``django.core.cache.caches``: the Channels middleware tests
# (``tests/unit/core/middleware/test_channels.py``) patch
# ``cache._cache.get_client`` directly and require the real Redis backend.
# Because the registry is not reset, the ``settings.CACHES`` LocMem patch
# below is inert against that live instance — it only affects code that
# reads ``settings.CACHES`` directly.
settings.CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    },
}

# Every pytest-xdist worker shares that single Redis instance. Isolate them
# by namespacing every default-cache key with the worker id. Without this,
# constant keys (e.g. loyalty's ``_TIER_LEVEL_CACHE_KEY`` map) and PK-keyed
# values (e.g. the parler translation cache, whose PKs collide across the
# per-worker test databases) leak between workers and produce
# order-dependent flakes. The teardown clear below deletes only this
# worker's namespace so it never FLUSHDBs another worker's live keys.
CACHE_WORKER_PREFIX = "test_%s" % os.environ.get(
    "PYTEST_XDIST_WORKER", "master"
)
caches["default"].key_prefix = CACHE_WORKER_PREFIX

# The Redis-backed instance the whole suite is meant to use. Captured
# here, while it is still the one built at app-load, so it can be put
# back after any test that swapped it out.
_ORIGINAL_DEFAULT_CACHE = caches["default"]


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
import extra_settings.models as _extra_settings_models  # noqa: E402

_extra_settings_cache._get_cache = _dummy_extra_settings_cache


# Belt-and-braces: also patch the public cache API functions themselves
# so that ANY consumer that captured a reference to them at import time
# still sees a cache miss for get / a no-op for set. The ``_get_cache``
# patch above is the canonical path, but in CI under shared Redis +
# parallel xdist, isolated workers have intermittently observed stale
# cache reads — bypassing the cache helpers entirely at the public-API
# level eliminates that race surface and forces every ``Setting.get``
# to consult Postgres (which IS rolled back per test).
def _noop_get_cached_setting(_key):
    return None


def _noop_set_cached_setting(_key, _value):
    return None


def _noop_del_cached_setting(_key):
    return None


_extra_settings_cache.get_cached_setting = _noop_get_cached_setting
_extra_settings_cache.set_cached_setting = _noop_set_cached_setting
_extra_settings_cache.del_cached_setting = _noop_del_cached_setting

# These were already imported into ``extra_settings.models`` at module
# load (``from extra_settings.cache import get_cached_setting,
# set_cached_setting``), so patching ``extra_settings.cache.*`` after
# the fact does not affect the captured references. Re-bind on the
# models module too.
_extra_settings_models.get_cached_setting = _noop_get_cached_setting
_extra_settings_models.set_cached_setting = _noop_set_cached_setting

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
# ``synchronous_commit=off`` lets Postgres ack a COMMIT before the WAL record
# is fsync'd to disk. It is crash-safe (no corruption — at most a few of the
# very last commits are lost on a crash, which a throwaway test DB never
# needs), and it markedly cuts commit/DDL latency across the migration replay
# and the per-test transaction churn. Set per-session here so it applies to the
# test DB only, never production.
_test_db_options["options"] = (
    "-c statement_timeout=0 -c idle_in_transaction_session_timeout=0"
    " -c synchronous_commit=off"
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


def _reset_worker_cache():
    """Clear only THIS worker's cache namespace after each test.

    Best-effort by design: see the ConnectionError handling at the end.

    A blanket ``cache.clear()`` is a Redis ``FLUSHDB``; on the shared test
    Redis that wipes other workers' live keys mid-test, turning their
    ``assertNumQueries`` cache-hit assertions into flaky misses. Deleting
    only the worker-prefixed keys keeps each worker's teardown local. Falls
    back to a plain ``clear()`` for non-Redis backends (e.g. LocMem).
    """
    backend = caches["default"]
    get_client = getattr(getattr(backend, "_cache", None), "get_client", None)
    if get_client is None:
        backend.clear()
        return
    client = get_client(None, write=True)
    # make_tenant_key prepends the ACTIVE SCHEMA to every raw key
    # ({schema}:{key_prefix}:{version}:{key}), so the worker-namespace
    # pattern must tolerate the schema segment. Without it the per-test
    # clear silently matched nothing and stale entries (e.g. parler's
    # translation cache after a delete + same-PK recreate) leaked
    # between tests as order-dependent failures.
    patterns = (
        f"{backend.key_prefix}:*",  # non-tenant layouts (safety net)
        f"*:{backend.key_prefix}:*",  # {schema}:{prefix}:… tenant layout
    )
    # Redis connection errors here must not fail the test that just
    # passed. Under -n auto every worker holds pooled connections to the
    # same localhost Redis, and one occasionally comes back dead —
    # observed once as "ConnectionResetError [WinError 10054] ... while
    # reading from localhost:6379" raised at TEARDOWN of a channels test
    # whose assertions had all succeeded. Retry once (a fresh connection
    # is drawn from the pool), then give up: the only cost of skipping a
    # cleanup pass is that this worker's keys live until its next
    # teardown, and they are namespaced to this worker anyway.
    for attempt in (1, 2):
        try:
            keys: list = []
            for pattern in patterns:
                keys.extend(client.scan_iter(match=pattern, count=1000))
            if keys:
                client.delete(*keys)
            return
        except RedisConnectionError:
            if attempt == 2:
                return
            client = get_client(None, write=True)


@pytest.fixture(autouse=True)
def clear_caches():
    yield
    _reset_worker_cache()


@pytest.fixture(autouse=True)
def _assert_english_locale_if_marked(request):
    """Pin the active gettext translation to English for any test (or
    test class / test module) marked with ``@pytest.mark.assert_english``.

    The project default is ``LANGUAGE_CODE='el'`` and most validators /
    admin filter labels / error messages use ``gettext_lazy``. Tests
    that assert on English substrings of those messages
    (``"quantity"``, ``"greater"``, ``"insufficient stock"``, admin
    filter labels in English, etc.) pass in CI — which has no
    ``compilemessages`` step so falls back to the English source — but
    fail on dev machines where the compiled ``el`` ``.mo`` files are
    available and Django returns the translated Greek string.

    Opting in per-module via ``pytestmark = pytest.mark.assert_english``
    is the narrowest fix: it leaves parler / i18n tests alone (those
    rely on ``LANGUAGE_CODE='el'`` to read translated content), and
    keeps the override explicit so a future reader knows why the test
    runs in English.

    Every other test is pinned to the project default language for the
    duration of the test. Django's testing docs recommend resetting the
    active language between tests; without this, the active gettext language
    depended on whatever a prior test left active (LocaleMiddleware requests,
    an un-restored ``translation.activate``, …), so a test asserting English
    message text passed or failed non-deterministically with execution order.
    Pinning the default makes unmarked tests deterministic and surfaces any
    English-asserting test that is missing the ``assert_english`` marker.
    """
    from django.utils import translation

    language = (
        "en"
        if request.node.get_closest_marker("assert_english")
        else settings.LANGUAGE_CODE
    )
    with translation.override(language):
        yield


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
    """Clear this worker's cache namespace before tests that use the DB.

    Uses the worker-scoped clear (not a global FLUSHDB) so a before-test
    clear on one worker cannot evict another worker's live keys.
    """
    if request.node.get_closest_marker("django_db"):
        try:
            _reset_worker_cache()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_payment_events_redis_client():
    """Drop the cached Redis client between tests so per-test
    ``patch('redis.Redis')`` mocks aren't bypassed by a stale instance
    saved by an earlier test in the same process.
    """
    import order.payment_events as payment_events_module

    payment_events_module._redis_client = None
    yield
    payment_events_module._redis_client = None


@pytest.fixture(autouse=True)
def _reseed_extra_settings(request):
    """Reset every ``EXTRA_SETTINGS_DEFAULTS`` row to its declared
    baseline before each DB test.

    Why **reset** and not just **re-seed**: tests that mutate a
    setting (e.g. ``Setting.objects.update_or_create(name="ACS_SMARTPOINT_ENABLED",
    ...)``) and run with ``@pytest.mark.django_db(transaction=True)``
    commit the change to the shared test database. Under ``-n auto``
    a subsequent test on a different worker reads the mutated value
    and the assertion flakes. The previous version called
    ``Setting.set_defaults_from_settings()`` which uses
    ``get_or_create`` and only writes on creation — it restored
    missing rows after a ``flush`` but didn't undo value mutations.

    This version uses ``update_or_create`` keyed on ``name`` and
    rewrites ``value_<type>`` to the declared default every time, so
    each test starts from the same baseline regardless of what any
    prior test did. The cost is one tiny UPDATE per declared setting
    (~30 rows) per test — dwarfed by the EAGER signal cascades the
    same tests trigger.

    Notes:

    * ``value_type`` is normalised the same way ``extra_settings``
      does in its own ``set_defaults`` — strips the ``Setting.TYPE_``
      prefix if present and lowercases — so both the long and short
      forms in ``EXTRA_SETTINGS_DEFAULTS`` are accepted.
    * Settings not declared in ``EXTRA_SETTINGS_DEFAULTS`` (rare —
      ad-hoc admin-created rows) are left alone.
    * The DummyCache patch above neutralises ``extra_settings``'s
      caching; combined with this reset, every ``Setting.get`` is a
      direct, current-test DB read with no cross-worker leakage.
    """
    if not request.node.get_closest_marker("django_db"):
        return
    from django.conf import settings as _dj_settings

    try:
        from extra_settings.models import Setting

        for default in getattr(_dj_settings, "EXTRA_SETTINGS_DEFAULTS", ()):
            name = default.get("name")
            value_type = (
                default.get("type", "string")
                .replace("Setting.TYPE_", "")
                .lower()
            )
            value = default.get("value")
            if not name or value is None:
                continue
            Setting.objects.update_or_create(
                name=name,
                defaults={
                    "value_type": value_type,
                    f"value_{value_type}": value,
                },
            )
    except Exception:
        # Fixture is best-effort — a transient DB connection error
        # must not mask the real failure of the test itself.
        pass


@pytest.fixture(autouse=True)
def _reseed_shipping_providers(request):
    """Restore the ``ShippingProvider`` seed rows for every DB test.

    The ``shipping/migrations/0002_seed_providers.py`` data migration
    only runs once at DB creation. Same issue as the
    ``_reseed_extra_settings`` fixture above: any test marked
    ``@pytest.mark.django_db(transaction=True)`` flushes every table on
    teardown, wiping the ``acs`` / ``boxnow`` rows.

    Subsequent tests that ``ShippingProvider.objects.get(code="acs")``
    (e.g. via the carrier registry, the order serializer, or the
    ``available_options`` view) then explode with ``DoesNotExist`` —
    one or two unlucky tests at random per ``-n auto`` run.

    Idempotent: ``update_or_create`` is a no-op when the seed rows
    are still in place, restorative when they are not.
    """
    if request.node.get_closest_marker("django_db"):
        try:
            from shipping.models import ShippingProvider

            ShippingProvider.objects.update_or_create(
                code="boxnow",
                defaults={
                    "name": "BOX NOW",
                    "is_active": False,
                    "supports_home_delivery": False,
                    "supports_pickup_point": True,
                    "live_mode": False,
                    "priority": 20,
                    "metadata": {
                        "supported_countries": ["GR"],
                        "locker_picker_kind": "boxnow_widget",
                        "tagline_key": "shipping.method.boxnow.tagline",
                        "tagline_color": "info",
                        "logo": "/img/shipping/boxnow.png",
                        "uses_generic_picker": False,
                    },
                },
            )
            ShippingProvider.objects.update_or_create(
                code="acs",
                defaults={
                    "name": "ACS Courier",
                    "is_active": False,
                    "supports_home_delivery": True,
                    "supports_pickup_point": True,
                    "live_mode": False,
                    "priority": 10,
                    # Mirror the seed in ``shipping/migrations/
                    # 0004_seed_provider_metadata.py``. Keep these in
                    # sync — the metadata-driven config tests rely on
                    # the keys being present on every test row, and
                    # production reads the same keys.
                    "metadata": {
                        "supported_countries": ["GR"],
                        "locker_picker_kind": "acs_db_picker",
                        "logo": "/img/shipping/acs.png",
                        "shop_kinds_by_country": {
                            "GR": [7, 8],
                            "CY": [7],
                        },
                        "nearest_limit": 20,
                        "min_weight_kg": "0.5",
                        "max_weight_kg": "999",
                        "default_voucher_language": "GR",
                        "print_type": 1,
                        "default_map_center": [37.9838, 23.7275],
                        "default_map_zoom": 11,
                        "tile_provider": {
                            "light": {
                                "url": (
                                    "https://{s}.basemaps.cartocdn.com/"
                                    "light_all/{z}/{x}/{y}{r}.png"
                                ),
                                "attribution": "© OSM © CARTO",
                                "max_zoom": 19,
                                "subdomains": "abcd",
                            },
                            "dark": {
                                "url": (
                                    "https://{s}.basemaps.cartocdn.com/"
                                    "dark_all/{z}/{x}/{y}{r}.png"
                                ),
                                "attribution": "© OSM © CARTO",
                                "max_zoom": 19,
                                "subdomains": "abcd",
                            },
                        },
                    },
                },
            )
        except Exception:
            # The fixture is best-effort — a transient DB connection
            # error must not mask the real failure of the test itself.
            pass


@pytest.fixture
def bind_tenant(monkeypatch):
    """Attach a tenant (or a lightweight stand-in) to ``connection.tenant``
    for the duration of a single test.

    ``TenantMainMiddleware`` is stripped for tests (see above), so
    ``connection.tenant`` is unset by default — the tenant credential
    helpers (``tenant.credentials``) then treat every third-party
    integration as unconfigured. Tests exercising tenant-scoped
    behaviour bind a fake tenant here; ``monkeypatch`` unwinds it after
    the test so parallel xdist workers never see leaked state. Several
    test modules also declare their own local copy of this fixture
    (same shape) — either is fine, the local one simply shadows this
    one for that module.

    Only for stand-ins and for code that never switches schema. A real
    ``Tenant`` whose code path enters ``schema_context`` (every eager
    ``TenantTask`` does) must be bound with
    ``tests.utils.staff.bind_store_tenant``: the context exit restores
    the connection via ``set_tenant(previous)``, which rewrites
    ``connection.schema_name`` to the bound tenant, and unwinding the
    attribute alone leaves the worker outside the public schema.
    """

    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)

    return _bind


@pytest.fixture
def acs_configured_tenant(bind_tenant):
    """Bind a fake tenant with usable ACS credentials.

    ACS credentials are tenant-only (no settings fallback — see
    ``tenant/credentials.py:acs_credentials()``), so ``AcsCarrier.
    is_kind_enabled()``, the ACS fanout Celery tasks, and any test
    exercising ACS availability need an active tenant with
    ``Tenant.acs_*`` fields set. A ``SimpleNamespace`` is enough — the
    credential helper only ever ``getattr()``s the specific field
    names off ``connection.tenant``.
    """
    from types import SimpleNamespace

    tenant = SimpleNamespace(
        schema_name="test-acs-tenant",
        acs_api_key="TEST_ACS_KEY",
        acs_company_id="TEST_ACS_COMPANY",
        acs_company_password="TEST_ACS_PASSWORD",
        acs_user_id="TEST_ACS_USER",
        acs_user_password="TEST_ACS_USER_PASSWORD",
        acs_billing_code="2ΑΚ89587",
        acs_station_origin="",
    )
    bind_tenant(tenant)
    return tenant


_BOXNOW_TENANT_FIELDS: dict = {
    "box_now_client_id": "TEST_BOXNOW_CLIENT",
    "box_now_client_secret": "TEST_BOXNOW_SECRET",
    "box_now_partner_id": "12345",
    "box_now_warehouse_id": "2",
    "box_now_notify_phone": "+302100000000",
    "box_now_webhook_secret": "TEST_BOXNOW_WHS",
}


@pytest.fixture
def boxnow_configured_tenant(bind_tenant):
    """Bind a fake tenant with usable BoxNow credentials.

    BoxNow credentials are tenant-only (no settings fallback — see
    ``tenant/credentials.py:box_now_credentials()``), so ``BoxNowCarrier.
    is_kind_enabled()``, the BoxNow fanout Celery tasks, and any test
    exercising BoxNow availability need an active tenant with
    ``Tenant.box_now_*`` fields set.
    """
    from types import SimpleNamespace

    tenant = SimpleNamespace(
        schema_name="test-boxnow-tenant", **_BOXNOW_TENANT_FIELDS
    )
    bind_tenant(tenant)
    return tenant


@pytest.fixture
def acs_and_boxnow_configured_tenant(bind_tenant):
    """Bind a single fake tenant with BOTH ACS and BoxNow credentials.

    ``bind_tenant`` replaces ``connection.tenant`` wholesale, so a test
    that needs both carriers available can't just request
    ``acs_configured_tenant`` AND ``boxnow_configured_tenant`` — the
    second call would clobber the first. Use this combined fixture
    instead.
    """
    from types import SimpleNamespace

    tenant = SimpleNamespace(
        schema_name="test-acs-boxnow-tenant",
        acs_api_key="TEST_ACS_KEY",
        acs_company_id="TEST_ACS_COMPANY",
        acs_company_password="TEST_ACS_PASSWORD",
        acs_user_id="TEST_ACS_USER",
        acs_user_password="TEST_ACS_USER_PASSWORD",
        acs_billing_code="2ΑΚ89587",
        acs_station_origin="",
        **_BOXNOW_TENANT_FIELDS,
    )
    bind_tenant(tenant)
    return tenant


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


@pytest.fixture(scope="session", autouse=True)
def _widen_meilisearch_timeout():
    """Apply the test timeout to the LIVE Meilisearch clients.

    Setting ``settings.MEILISEARCH["TIMEOUT"]`` is not enough on its
    own: ``meili._client.client`` is a module-level singleton built
    during ``django.setup()`` — i.e. BEFORE this conftest runs — so it
    and its inner ``meilisearch.Client`` have already captured the old
    value on ``config.timeout``. Verified by probe: settings said 60
    while ``client.settings.timeout`` was still 10.
    """
    try:
        from meili._client import client as meili_client
    except Exception:  # pragma: no cover - meili not importable
        yield
        return

    _retry_meili_connection_errors()

    targets = [meili_client, getattr(meili_client, "client", None)]
    targets.append(getattr(meili_client, "search_client", None))
    for obj in targets:
        if obj is None:
            continue
        cfg = getattr(obj, "config", None)
        if cfg is not None and hasattr(cfg, "timeout"):
            cfg.timeout = MEILI_TEST_TIMEOUT
        st = getattr(obj, "settings", None)
        if st is not None and hasattr(st, "timeout"):
            # _MeiliSettings is a frozen dataclass — normal assignment
            # raises FrozenInstanceError. config.timeout above is the
            # value that actually reaches the HTTP layer (it is the one
            # quoted in "Read timed out. (read timeout=N)"); this is
            # kept in step only so the two cannot disagree.
            try:
                object.__setattr__(st, "timeout", MEILI_TEST_TIMEOUT)
            except Exception:  # pragma: no cover - defensive
                pass
    yield


@pytest.fixture(autouse=True)
def _restore_default_cache_backend():
    """Undo cache-backend leakage between tests.

    ``override_settings(CACHES=LocMem)`` (the dashboard caching tests)
    makes ``CacheHandler`` resolve and CACHE a LocMemCache. Django
    restores the *settings* when the override exits, but the resolved
    instance stays in ``caches._connections``, so later tests on the
    same worker keep getting LocMem. Anything expecting the Redis API
    then dies on an attribute that backend does not have —
    ``'LocMemCache' object has no attribute 'keys'`` in
    core/cache/service.py, and ``OrderedDict has no attribute
    'get_client'`` in the channels middleware. Both were reproducible by
    running tests/unit/admin/test_dashboard.py immediately before the
    affected file.

    Deleting the entry is NOT a fix: tests/conftest.py deliberately
    leaves ``settings.CACHES`` pointing at LocMem, so a rebuild would
    hand back LocMem again. Restore the captured Redis instance instead.
    """
    yield
    try:
        if caches._connections.default is not _ORIGINAL_DEFAULT_CACHE:
            caches._connections.default = _ORIGINAL_DEFAULT_CACHE
    except AttributeError:
        caches._connections.default = _ORIGINAL_DEFAULT_CACHE


def _retry_meili_connection_errors(attempts: int = 3) -> None:
    """Retry Meilisearch calls that fail with a dropped connection.

    ``@requires_meilisearch`` tests talk to a real local engine, and the
    client uses module-level ``requests.get``/``requests.post`` — no
    Session, so every call opens a fresh connection. Under ``-n auto``
    that connection churn occasionally loses one:

        MeilisearchCommunicationError, ('Connection aborted.',
        ConnectionResetError(10054, 'An existing connection was
        forcibly closed by the remote host'))

    Seen once in five full parallel runs, on a test that is green at
    -n0. Only ``MeilisearchCommunicationError`` is retried — that maps
    to ``requests.exceptions.ConnectionError``, i.e. the request did not
    complete, so replaying it cannot double-apply anything. Timeouts are
    deliberately NOT retried: there the server may already have acted.
    """
    from meilisearch import _httprequests
    from meilisearch.errors import MeilisearchCommunicationError

    original = _httprequests.HttpRequests.send_request
    if getattr(original, "_retry_wrapped", False):
        return

    def send_request(self, *args, **kwargs):
        for remaining in range(attempts - 1, -1, -1):
            try:
                return original(self, *args, **kwargs)
            except MeilisearchCommunicationError:
                if remaining == 0:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    send_request._retry_wrapped = True  # type: ignore[attr-defined]
    _httprequests.HttpRequests.send_request = send_request
