"""The suite must configure itself the same way on every machine.

Two regressions, both invisible in any single run:

* ``DISABLE_CACHE`` came from the developer's ``.env`` (True locally,
  unset in CI) and was only pinned by ``tests/conftest.py`` — which
  pytest-django imports AFTER ``django.setup()``, i.e. after
  ``CoreConfig.ready()`` had already decided whether to connect the
  cache-invalidation receivers. CI ran with them, local runs without.
  ``tests/settings.py`` now pins it, and the other switches ``.env``
  flipped, before ``settings.py`` is even imported.

* ``tests/conftest.py`` pointed ``settings.CACHES`` at LocMem while the
  materialised backend was Redis. Whenever Django rebuilt the cache
  registry — on entering and leaving every ``override_settings(CACHES=
  ...)`` — the rebuild followed the settings, so the first test after
  such a class ran on LocMem. The settings now describe the backend the
  suite actually uses.

The tests below run in file order on one xdist worker (``--dist
loadfile``), which the last one relies on.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.cache import caches
from django.db.models.signals import post_delete, post_save
from django.test import SimpleTestCase, override_settings

from core.cache.invalidation import _dispatch_uid, _iter_declared_models
from tests.cache import WorkerScopedCache

_WORKER_PREFIX = f"test_{os.environ.get('PYTEST_XDIST_WORKER', 'master')}"

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "suite-configuration-test",
    }
}


def _connected_invalidation_uids() -> set[str]:
    connected = {
        lookup_key[0]
        for signal in (post_save, post_delete)
        for lookup_key, *_ in signal.receivers
    }
    declared = {
        _dispatch_uid(code, label) for code, label, _ in _iter_declared_models()
    }
    return connected & declared


def test_the_suite_runs_on_the_test_settings_module():
    """Pinned with ``--ds`` in ``addopts`` — the highest-precedence
    source, so a ``DJANGO_SETTINGS_MODULE`` in the environment (every
    container started with ``env_file: .env``) cannot swap it out."""
    assert settings.SETTINGS_MODULE == "tests.settings"


def test_environment_switches_are_pinned_before_settings_load():
    """Each of these came from the developer's ``.env`` and differed
    from CI. They are read when ``settings.py`` is imported, so what
    they derive is checked too, not just the flag."""
    assert settings.DISABLE_CACHE is True
    assert settings.ENABLE_DEBUG_TOOLBAR is False
    assert "debug_toolbar" not in settings.INSTALLED_APPS
    assert not any("debug_toolbar" in m for m in settings.MIDDLEWARE)
    assert settings.APPEND_SLASH is False
    # DEBUG itself is turned off for requests by tests/conftest.py; its
    # import-time value decides the throttle rates.
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["anon"] is None


def test_invalidation_receivers_are_not_connected_at_startup():
    """``CoreConfig.ready()`` read ``DISABLE_CACHE`` from the environment,
    so in CI every Product, Category or PayWay write ran
    ``CacheService.purge``. The module that tests the wiring connects
    them itself (``connect_surface_invalidation(force=True)``)."""
    assert _connected_invalidation_uids() == set()


def test_the_default_cache_is_the_worker_scoped_redis_backend():
    backend = caches["default"]

    assert type(backend) is WorkerScopedCache
    assert backend.key_prefix == _WORKER_PREFIX
    assert (
        settings.CACHES["default"]["BACKEND"] == "tests.cache.WorkerScopedCache"
    )


@override_settings(CACHES=_LOCMEM)
class ClassLevelCacheOverrideTests(SimpleTestCase):
    """Every test in the class must see the override — the per-test
    restore fixture used to put Redis back after the first one."""

    def test_first_test_sees_the_override(self):
        self.assertEqual(type(caches["default"]).__name__, "LocMemCache")

    def test_second_test_still_sees_the_override(self):
        self.assertEqual(type(caches["default"]).__name__, "LocMemCache")


def test_the_first_test_after_a_class_override_is_back_on_redis():
    """``tearDownClass`` exits the override after the last test's own
    teardown, and Django then rebuilds the registry from settings."""
    backend = caches["default"]

    assert type(backend) is WorkerScopedCache
    assert backend.key_prefix == _WORKER_PREFIX
