"""Django settings for the pytest suite — both ``tests/`` and ``tests_mt/``.

Selected with ``--ds=tests.settings`` in ``pyproject.toml``'s
``addopts``. pytest-django gives the ``--ds`` option precedence over a
``DJANGO_SETTINGS_MODULE`` environment variable, which in turn beats the
ini key (``pytest_django/plugin.py``, ``_get_option_with_source``) — and
every container started with ``env_file: .env`` has that variable set
to ``settings``, so only the option is reliable.

This module is for the values that must be right BEFORE
``django.setup()``. pytest-django sets Django up in its
``pytest_load_initial_conftests`` hook, and pytest imports the
conftests only after that (``_pytest/config/__init__.py``, its own
implementation is ``trylast``), so by the time ``tests/conftest.py``
assigns a setting every ``AppConfig.ready()`` has already read it and
the cache registry has already been built. Settings that are only read
at request time stay in the conftests.

``settings.py`` reads its values from the environment, filled from
``.env`` by ``dotenv.load_dotenv`` — which never overrides a variable
that is already set (python-dotenv's ``override=False`` default). So
the switches the suite depends on are put in the environment BEFORE
``settings.py`` is imported: ``.env`` then cannot change them, and
everything ``settings.py`` derives from them (``INSTALLED_APPS``,
``MIDDLEWARE``, throttle rates) follows. Each one differed between a
developer machine and CI.
"""

from os import environ
from pathlib import Path
from shutil import rmtree
from tempfile import gettempdir

_PINNED_ENVIRONMENT = {
    # Nothing is response-cached under pytest (``core.utils.views
    # .cache_methods`` is a no-op there), so there is nothing to
    # invalidate: ``CoreConfig.ready()`` leaves the cache-invalidation
    # receivers disconnected, and ``tests/integration/core/
    # test_cache_auto_invalidation.py`` connects them itself. It was
    # True from ``.env`` and unset (False) in CI, so every Product,
    # Category or PayWay write ran ``CacheService.purge`` in CI only.
    "DISABLE_CACHE": "True",
    # Production's value (``backend-config.yaml``). From ``.env`` it
    # added ``debug_toolbar`` to ``INSTALLED_APPS`` and its middleware
    # to every request, locally only.
    "ENABLE_DEBUG_TOOLBAR": "False",
    # Production's value. True from ``.env``, so a request for a URL
    # without its trailing slash was redirected locally and 404'd in CI.
    "APPEND_SLASH": "False",
    # What CI sets and ``.env`` ships. Read at import time for the DRF
    # throttle rates (``None`` under DEBUG) long before
    # ``tests/conftest.py`` turns ``settings.DEBUG`` off for requests;
    # an ``.env`` with DEBUG off would throttle the whole suite.
    "DEBUG": "True",
}
environ.update(_PINNED_ENVIRONMENT)

from settings import *  # noqa: E402, F403
from settings import REDIS_URL  # noqa: E402

# The backend the suite really runs on, declared here rather than
# patched onto the instance after start-up. Django rebuilds the
# registry from ``settings.CACHES`` whenever an ``override_settings(
# CACHES=...)`` is entered or left (``django.test.signals
# .clear_cache_handlers``); with the settings describing some other
# backend, every test after such a class ran on that one instead.
#
# ``KEY_PREFIX`` gives each xdist worker its own namespace in the one
# shared Redis database; without it constant keys (loyalty's
# ``_TIER_LEVEL_CACHE_KEY``) and PK-keyed values (parler's translation
# cache, whose PKs collide across the per-worker test databases) leak
# between workers as order-dependent failures. pytest-xdist exports ``PYTEST_XDIST_WORKER``
# before the worker parses its configuration (``xdist/remote.py``), so
# it is set by the time this module is imported. ``KEY_FUNCTION``
# matches production, so the suite exercises the tenant-scoped keys it
# ships with. ``VERSION`` and ``TIMEOUT`` stay at Django's defaults
# rather than following ``DEFAULT_CACHE_*`` from the environment.
#
# The database name joins the worker id because two pytest sessions on
# one machine (a full run beside a targeted one, each with its own
# ``DB_NAME``) otherwise share a namespace — and clear each other's.
TEST_NAMESPACE = (
    f"{DATABASES['default']['NAME']}"  # noqa: F405
    f"_{environ.get('PYTEST_XDIST_WORKER', 'master')}"
)
CACHES = {
    "default": {
        "BACKEND": "tests.cache.WorkerScopedCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": f"test_{TEST_NAMESPACE}",
        "KEY_FUNCTION": "tenant.cache.make_tenant_key",
    },
}

# Uploads go to a per-worker tree under the OS temp directory, emptied
# whenever a worker starts. ``settings.py`` points MEDIA_ROOT at the
# repository's ``mediafiles/``, the tree the development server serves,
# and the suite never deletes what it uploads: every run added its
# images there, 4.5 million files by 2026-09-26, and a test that walks
# the repository took past its timeout on that tree alone. The private
# root follows (``tenant.storage.private_media_root``: MEDIA_ROOT with a
# ``_private`` suffix). Per namespace, so one worker's or session's
# wipe cannot delete another's files mid-test.
MEDIA_ROOT = str(
    Path(gettempdir()) / "grooveshop-tests" / TEST_NAMESPACE / "mediafiles"
)
for _root in (MEDIA_ROOT, MEDIA_ROOT + "_private"):
    rmtree(_root, ignore_errors=True)
