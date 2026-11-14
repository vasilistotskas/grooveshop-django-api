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

import os
from unittest import mock

import pytest
from django.conf import settings
from django_tenants.management.commands.migrate_schemas import (
    MigrateSchemasCommand as _MigrateSchemasCommand,
)
from django_tenants.utils import get_public_schema_name

from tests.db_lifecycle import django_db_keepdb  # noqa: F401
from tests.utils.emails import catalogue, probe_templates  # noqa: F401

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

# Everything below is left at the settings package's default on purpose:
# DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"] and
# TenantMainMiddleware present. CACHES and DISABLE_CACHE come from
# ``tests/settings.py`` (``--ds`` in pyproject.toml, shared by both
# lanes): the Redis-backed ``tests.cache.WorkerScopedCache`` — a
# ``CustomCache`` whose ``clear()`` is scoped to this run's namespace —
# with the production KEY_FUNCTION = tenant.cache.make_tenant_key.


@pytest.fixture(autouse=True)
def _connection_back_to_public():
    """Start every test in the public schema.

    A ``TenantClient`` request leaves the shared connection bound to the
    store it hit (``TenantMainMiddleware`` sets it, nothing unsets it),
    so the next test would run inside that store's schema: saving the
    other tenant's row then fails with "Can't update tenant outside it's
    own schema", depending only on the order the files run in.
    """
    yield
    from django.db import connection

    connection.set_schema_to_public()


# ── the store schemas are built side by side ────────────────────────
# A store schema costs a full migration replay, about 160 s on a CI
# runner, and the lane has three: the seeded ``webside`` and the two
# below. Built one after the other (``webside`` by the ``migrate`` that
# creates the test database, the other two by ``Tenant.save()``) they
# were 8 of the lane's 12 minutes on 2026-11-10, and the 251 tests one.
# So that ``migrate`` now builds the public schema only, and
# ``mt_store_schemas`` builds the three stores at once.
_migrate_as_asked = _MigrateSchemasCommand.handle


def _migrate_public_unless_stores_are_asked_for(self, *args, **options):
    if not (options.get("tenant") or options.get("schema_name")):
        options = {**options, "shared": True}
    return _migrate_as_asked(self, *args, **options)


_MigrateSchemasCommand.handle = _migrate_public_unless_stores_are_asked_for

# The one tenant this whole lane shares.
MT_TENANT_SCHEMA = "mt_smoke"
MT_TENANT_DOMAIN = "mt-smoke.test"

# A SECOND tenant, for the tests that need two real schemas side by
# side (a token issued by one store presented to another).
MT_TENANT_B_SCHEMA = "mt_smoke_b"
MT_TENANT_B_DOMAIN = "mt-smoke-b.test"

_MT_STORES = {
    MT_TENANT_SCHEMA: {
        "domain": MT_TENANT_DOMAIN,
        "name": "MT Smoke Tenant",
        "slug": "mt-smoke",
        "owner_email": "mt-smoke@example.com",
    },
    MT_TENANT_B_SCHEMA: {
        "domain": MT_TENANT_B_DOMAIN,
        "name": "MT Smoke Tenant B",
        "slug": "mt-smoke-b",
        "owner_email": "mt-smoke-b@example.com",
    },
}


@pytest.fixture(scope="session")
def mt_store_schemas(django_db_setup, django_db_blocker):
    """The lane's two tenants, their schemas migrated with ``webside``'s.

    ``TenantMixin.save()`` would create each schema and replay the whole
    migration history into it, one store after the other. Here the row
    is saved without that, the schema is created empty, and
    ``migrate_schemas --tenant`` then migrates every store with
    django-tenants' ``subprocess`` executor: one
    ``manage.py migrate_schemas --schema <name>`` process per store, the
    command a deploy runs, all at the same time. Each schema is still
    built from the first migration to the last.

    The child processes read their settings from the environment, so
    ``DB_NAME`` names the test database while they run. They do not see
    what this file assigns to ``settings``; ``MEILI_OFFLINE`` gives them
    the one assignment a migration could reach.

    With ``--reuse-db`` the schemas exist already and the same call
    applies only the migrations added since.
    """
    from django.core.management import call_command
    from django.db import connection
    from django_tenants.utils import schema_exists

    from tenant.models import Tenant, TenantDomain

    with django_db_blocker.unblock():
        connection.set_schema_to_public()
        tenants = {}
        for schema_name, store in _MT_STORES.items():
            tenant = Tenant.objects.filter(schema_name=schema_name).first()
            if tenant is None:
                tenant = Tenant(
                    schema_name=schema_name,
                    name=store["name"],
                    slug=store["slug"],
                    owner_email=store["owner_email"],
                )
                tenant.auto_create_schema = False
                tenant.save()
            TenantDomain.objects.get_or_create(
                domain=store["domain"],
                defaults={"tenant": tenant, "is_primary": True},
            )
            assert tenant.get_primary_domain() is not None, (
                f"tenant '{schema_name}' has no primary domain — "
                "TenantClient needs one to set the Host header."
            )
            if not schema_exists(schema_name):
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"CREATE SCHEMA {connection.ops.quote_name(schema_name)}"
                    )
            tenants[schema_name] = tenant

        child_environment = {
            "DB_NAME": connection.settings_dict["NAME"],
            "MEILI_OFFLINE": "True",
        }
        with mock.patch.dict(os.environ, child_environment):
            call_command(
                "migrate_schemas",
                tenant=True,
                executor="subprocess",
                parallel=Tenant.objects.exclude(
                    schema_name=get_public_schema_name()
                ).count(),
                interactive=False,
            )
        connection.set_schema_to_public()
        yield tenants


@pytest.fixture(scope="session")
def mt_tenant(mt_store_schemas):
    """The one provisioned tenant this lane's smoke tests share.

    Session-scoped: every test that touches it should either use data
    that tolerates being shared (append-only assertions) or clean up
    after itself, same discipline as any other session fixture.
    """
    return mt_store_schemas[MT_TENANT_SCHEMA]


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


@pytest.fixture(scope="session")
def mt_tenant_b(mt_store_schemas):
    """The second tenant, built with the first (``mt_store_schemas``)."""
    return mt_store_schemas[MT_TENANT_B_SCHEMA]
