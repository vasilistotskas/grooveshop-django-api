"""The public schema is the control plane and holds no tenant tables.

``TenantSyncRouter.allow_migrate`` refuses every non-``SHARED_APPS``
migration when the schema is ``public``, so a tenant-only table can only
appear there by accident — and this database lineage proved it can: 145
of them survived the cutover as frozen, unmigratable residue, removed by
``manage.py drop_public_tenant_residue``.

The main ``tests/`` lane cannot see any of this. Its conftest strips
``DATABASE_ROUTERS`` and ``TenantMainMiddleware`` to keep the suite
fast, which is exactly what makes schema-binding faults invisible there
— and is why the first attempt at this cleanup, a migration, dropped
that lane's entire public schema during test-database setup.

The invariant is asserted about the SCHEMA, not the cleanup: whatever
creates a tenant-only table in ``public`` is caught here.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import get_public_schema_name

from tenant.app_labels import tenant_only_table_names


@pytest.mark.django_db
def test_no_tenant_only_table_exists_in_the_public_schema():
    public = get_public_schema_name()

    with connection.cursor() as cursor:
        cursor.execute(
            "select table_name from information_schema.tables "
            "where table_schema = %s and table_type = 'BASE TABLE'",
            [public],
        )
        present = {row[0] for row in cursor.fetchall()}

    residue = sorted(present & tenant_only_table_names())

    assert not residue, (
        f"{len(residue)} tenant-only table(s) in the {public} schema: "
        f"{residue[:8]}{' …' if len(residue) > 8 else ''}. The router "
        f"never migrates these, so they freeze at their creation shape "
        f"and a tenant query running in a public context silently "
        f"returns empty instead of failing."
    )


@pytest.mark.django_db
def test_the_control_planes_own_tables_are_present(mt_tenant):
    """Guards the other direction: the cleanup must not take the shared
    tables with it. ``user`` and ``sessions`` are in BOTH app lists, so
    they are exempt from the residue set by construction — this pins
    that they really do still exist."""
    public = get_public_schema_name()

    with connection.cursor() as cursor:
        for table in (
            "tenant_tenant",
            "tenant_tenantdomain",
            "user_useraccount",
            "django_session",
        ):
            cursor.execute("select to_regclass(%s)", [f"{public}.{table}"])
            assert cursor.fetchone()[0] is not None, (
                f"{public}.{table} is missing — the control plane cannot "
                f"authenticate or resolve a tenant without it"
            )
