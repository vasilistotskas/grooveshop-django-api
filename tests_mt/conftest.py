"""Pytest configuration for the MT (multi-tenant schema-binding) lane.

``tests_mt/`` is a SIBLING of ``tests/``, not a subdirectory, so
``tests/conftest.py`` — which deliberately strips multi-tenancy
(``DATABASE_ROUTERS = []``, no ``TenantMainMiddleware``) to keep the
6000+ test main suite fast — never applies here. That stripping is
also why schema-binding bugs are invisible to the main suite (real
prod incidents: the ``django_admin_log`` cross-schema FK, cache-key
leaks) — this lane exists to catch that class of regression, on the
real ``TenantSyncRouter`` + ``TenantMainMiddleware``.

Speed-only settings are copied verbatim from ``tests/conftest.py``
(MD5 password hasher, Meilisearch OFFLINE, Celery eager) — anything
that touches tenancy (routers, middleware, cache backend) is
deliberately NOT copied, since keeping those real is the entire point.

Not collected by the default ``uv run pytest`` run: ``pyproject.toml``
sets ``testpaths = ["tests"]``, so this directory is invisible to a
bare ``pytest`` invocation. Run it explicitly:
``uv run pytest tests_mt -n 0`` (serial — the lane is small, and
schema-switching state is process-global via ``connection``, so
xdist parallelism would need per-worker schemas for no benefit here).
"""

from __future__ import annotations

import pytest
from django.conf import settings

# ── its own test DATABASE, not just its own settings ────────────────
# Both lanes defaulted to ``test_<DB_NAME>``, and they build INCOMPATIBLE
# layouts in it: ``tests/`` runs with DATABASE_ROUTERS = [] so every
# app's tables land in ``public``, while this lane runs the real
# TenantSyncRouter, which keeps TENANT_APPS tables out of ``public``
# entirely. Whichever lane created the database last therefore decided
# whether this one's isolation assertions could hold — running the main
# suite first made ``test_model_write_isolation`` and
# ``test_b2b_flag_isolation`` fail, because ``public.product_product``
# existed after all. Diagnosed 2026-09-01; the tests were correct and
# the database underneath them was not.
#
# A distinct TEST NAME is the fix: pytest-django honours
# ``DATABASES[alias]["TEST"]["NAME"]`` when deciding what to create, so
# the two lanes can no longer clobber each other and neither needs
# ``--create-db`` to recover from the other.
settings.DATABASES["default"].setdefault("TEST", {})
settings.DATABASES["default"]["TEST"]["NAME"] = (
    f"test_mt_{settings.DATABASES['default']['NAME']}"
)

settings.PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
settings.MEILISEARCH["OFFLINE"] = True
settings.CELERY_TASK_ALWAYS_EAGER = True
settings.CELERY_TASK_EAGER_PROPAGATES = True
settings.DEBUG = False

# Everything below is left at the settings.py default on purpose:
# DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"],
# TenantMainMiddleware present, CACHES = the real Redis-backed
# core.caches.CustomCache with KEY_FUNCTION = tenant.cache.make_tenant_key.

# The one tenant this whole lane shares.
MT_TENANT_SCHEMA = "mt_smoke"
MT_TENANT_DOMAIN = "mt-smoke.test"


@pytest.fixture(scope="session")
def mt_tenant(django_db_setup, django_db_blocker):
    """The one provisioned tenant this lane's smoke tests share.

    Provisioned exactly like ``django_tenants.test.cases.TenantTestCase``
    does: instantiate the real (non-historical) ``Tenant`` model and
    ``save()`` it. ``TenantMixin.save()`` (``auto_create_schema=True``
    by default) creates the Postgres schema and runs
    ``migrate_schemas --schema=mt_smoke`` for it automatically — this
    IS the one expensive part of the lane (a full migration replay),
    done ONCE per session.

    Session-scoped: every test that touches it should either use data
    that tolerates being shared (append-only assertions) or clean up
    after itself, same discipline as any other session fixture.
    """
    with django_db_blocker.unblock():
        from tenant.models import Tenant, TenantDomain

        tenant, created = Tenant.objects.get_or_create(
            schema_name=MT_TENANT_SCHEMA,
            defaults={
                "name": "MT Smoke Tenant",
                "slug": "mt-smoke",
                "owner_email": "mt-smoke@example.com",
            },
        )
        TenantDomain.objects.get_or_create(
            domain=MT_TENANT_DOMAIN,
            defaults={"tenant": tenant, "is_primary": True},
        )
        assert tenant.get_primary_domain() is not None, (
            f"tenant '{MT_TENANT_SCHEMA}' has no primary domain — "
            "TenantClient needs one to set the Host header."
        )
        yield tenant


@pytest.fixture(scope="session")
def mt_public_tenant(django_db_setup, django_db_blocker):
    """A ``Tenant`` row representing the public schema itself.

    ``auto_create_schema=False`` — the public schema always exists
    already (it is where every SHARED_APPS migration lands), so there
    is nothing to provision; this only gives tests a real row to look
    up when code under test expects ``Tenant.objects.get(schema_name=
    get_public_schema_name())`` to resolve, mirroring
    ``django_tenants.test.cases.SubfolderTenantTestCase`` and
    ``tests/unit/core/management/commands/test_tenant_scoped_commands.
    py``'s ``_ensure_public_tenant_row`` helper.
    """
    with django_db_blocker.unblock():
        from django_tenants.utils import get_public_schema_name

        from tenant.models import Tenant

        public_schema = get_public_schema_name()
        tenant = Tenant.objects.filter(schema_name=public_schema).first()
        if tenant is None:
            tenant = Tenant(
                schema_name=public_schema,
                name="MT Lane Public",
                slug="mt-lane-public",
                owner_email="mt-lane-public@example.com",
            )
            tenant.auto_create_schema = False
            tenant.save()
        yield tenant
