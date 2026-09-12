"""Task results live in the public schema and nowhere else.

``django_celery_results`` used to sit in TENANT_APPS as well as
SHARED_APPS, so every tenant schema carried its own copy of the three
tables. That is not neutral: django-tenants puts the tenant schema
ahead of ``public`` on the search path, so a failure recorded by a task
running in tenant context would land in the tenant copy — invisible to
the control plane, and unreachable by ``celery.backend_cleanup``, which
beat dispatches with no tenant header and which therefore only ever
prunes ``public``.

``tenant/migrations/0039`` drops the copies an earlier TENANT_APPS entry
created. This asserts the invariant rather than the migration: a newly
provisioned tenant must not grow them back either, which is what the
TENANT_APPS removal buys.

The main ``tests/`` lane cannot see this — its conftest strips
``DATABASE_ROUTERS``, so every table lives in one physical schema there.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection
from django_tenants.utils import get_public_schema_name

CELERY_RESULT_TABLES = frozenset(
    {
        "django_celery_results_chordcounter",
        "django_celery_results_groupresult",
        "django_celery_results_taskresult",
    }
)


def _tables_in(schema: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "select table_name from information_schema.tables "
            "where table_schema = %s and table_type = 'BASE TABLE'",
            [schema],
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.mark.django_db
def test_a_provisioned_tenant_schema_has_no_task_result_tables(mt_tenant):
    stray = sorted(_tables_in(mt_tenant.schema_name) & CELERY_RESULT_TABLES)

    assert not stray, (
        f"tenant schema '{mt_tenant.schema_name}' carries {stray} — a task "
        "failing in this tenant's context would write there instead of "
        "public, where neither the control plane nor "
        "celery.backend_cleanup would ever see it again."
    )


@pytest.mark.django_db
def test_the_public_schema_does_carry_them(mt_tenant):
    """The other half of the invariant: removing the app from
    TENANT_APPS must not have removed it from SHARED_APPS too, or
    failures would have nowhere to go at all."""
    present = _tables_in(get_public_schema_name()) & CELERY_RESULT_TABLES

    assert present == CELERY_RESULT_TABLES, (
        "the public schema is missing "
        f"{sorted(CELERY_RESULT_TABLES - present)} — Celery has no table "
        "to record failures in."
    )


def test_the_app_is_shared_only():
    """The setting behind both assertions above, named directly so a
    reviewer reading TENANT_APPS sees why it is absent."""
    assert "django_celery_results" in settings.SHARED_APPS
    assert "django_celery_results" not in settings.TENANT_APPS
