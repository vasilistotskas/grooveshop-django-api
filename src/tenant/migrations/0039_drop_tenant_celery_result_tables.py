"""Remove the per-tenant ``django_celery_results`` tables.

``django_celery_results`` used to sit in TENANT_APPS as well as
SHARED_APPS, so every tenant schema carries a copy of its three tables.
Task results are public-schema only now (see the SHARED_APPS comment in
settings.py): only failures are stored, a failure is platform
operations rather than one store's data, and
``celery.backend_cleanup`` is dispatched by beat with no tenant header —
so it runs in public and could never have pruned a tenant-schema row.

Leaving the tables behind is not neutral. django-tenants puts the
tenant schema ahead of public on the search path, so an unqualified
INSERT from a task running in tenant context would still land in the
tenant copy: invisible to the control plane, and never cleaned.

The rows are dropped with the tables. That is safe here — production
and staging were checked on 2026-09-12 and every one of these tables
was empty in every schema, which is what the Redis result backend
guarantees. Rows would only exist in a local checkout that ran on the
``django-db`` default, where task history has no value.

The ``django_migrations`` bookkeeping goes too, so a schema that is
later asked to migrate this app again builds the tables rather than
believing it already has them.

Not reversible: recreating empty tables would restore the split-brain
this removes.
"""

from __future__ import annotations

from django.db import migrations

TABLES = (
    "django_celery_results_chordcounter",
    "django_celery_results_groupresult",
    "django_celery_results_taskresult",
)


def drop_tenant_copies(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT table_schema
            FROM information_schema.tables
            WHERE table_name = %s AND table_schema <> 'public'
            ORDER BY table_schema
            """,
            [TABLES[-1]],
        )
        schemas = [row[0] for row in cursor.fetchall()]

        for schema in schemas:
            # One statement for all three: Postgres resolves the foreign
            # keys between them when they are dropped together, so no
            # CASCADE is needed and nothing outside the list can be
            # taken down by accident.
            targets = ", ".join(f'"{schema}"."{table}"' for table in TABLES)
            cursor.execute(f"DROP TABLE IF EXISTS {targets}")
            cursor.execute(
                f'DELETE FROM "{schema}"."django_migrations" WHERE app = %s',
                ["django_celery_results"],
            )


class Migration(migrations.Migration):
    dependencies = [("tenant", "0038_tenant_vertical")]

    operations = [
        migrations.RunPython(
            drop_tenant_copies, migrations.RunPython.noop, elidable=False
        )
    ]
