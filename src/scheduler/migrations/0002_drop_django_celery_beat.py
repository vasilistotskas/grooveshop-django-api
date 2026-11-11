"""Drop what django-celery-beat left behind, now that nothing reads it.

0001 copied its state into ``scheduler_periodictask`` and kept its tables for
the release that was still serving and for a rollback to it. That release is
gone from production, so this is the contract step.

The tables exist only in ``public``: the app was in SHARED_APPS. Its
bookkeeping is in every schema, because django-tenants records a shared app's
migrations in each tenant schema too, and contenttypes and auth are in both
app lists: 21 ``django_migrations`` rows, 6 content types and 24 permissions
per schema on production (2026-11-07). ``scheduler`` is shared, so this runs
in ``public`` only and reaches every schema itself, as ``tenant`` 0039 did.

Nothing would recreate the rows: the app is not installed, so
``create_contenttypes`` and ``create_permissions`` no longer see it. Left
behind, the admin lists permissions for models that do not exist, and a
reinstalled package would believe its tables are there.

What points at those rows goes the way Django's ``on_delete`` would take it:
group and user permissions and tagged items are deleted (CASCADE), admin log
entries keep their text with no content type (SET_NULL). Those are every
foreign key to ``django_content_type`` and ``auth_permission`` on production;
the constraints are deferred, so one added later fails this migration at
commit instead of leaving a dangling row.

Not reversible: the package is gone, and its rows are in the backups.
"""

from django.db import migrations

DROP_TABLES = """
DROP TABLE IF EXISTS
    django_celery_beat_periodictask,
    django_celery_beat_periodictasks,
    django_celery_beat_crontabschedule,
    django_celery_beat_intervalschedule,
    django_celery_beat_solarschedule,
    django_celery_beat_clockedschedule;
"""

APP = "django_celery_beat"

#: (table, column) pointing at ``auth_permission``, deleted with it.
PERMISSION_LINKS = (
    ("auth_group_permissions", "permission_id"),
    ("user_useraccount_user_permissions", "permission_id"),
)
#: (table, column, deleted) pointing at ``django_content_type``; the rest is
#: set to NULL.
CONTENT_TYPE_LINKS = (
    ("tag_taggeditem", "content_type_id", True),
    ("django_admin_log", "content_type_id", False),
)


def forget_django_celery_beat(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema FROM information_schema.tables
            WHERE table_name = 'django_migrations' ORDER BY table_schema
            """
        )
        schemas = [row[0] for row in cursor.fetchall()]
        for schema in schemas:
            forget_in(cursor, schema)


def forget_in(cursor, schema: str) -> None:
    def exists(table: str) -> bool:
        cursor.execute(
            "SELECT to_regclass(%s) IS NOT NULL", [f'"{schema}"."{table}"']
        )
        return cursor.fetchone()[0]

    cursor.execute(
        f'DELETE FROM "{schema}"."django_migrations" WHERE app = %s', [APP]
    )
    if not exists("django_content_type"):
        return
    content_types = (
        f'SELECT id FROM "{schema}"."django_content_type" WHERE app_label = %s'
    )
    if exists("auth_permission"):
        permissions = (
            f'SELECT id FROM "{schema}"."auth_permission" '
            f"WHERE content_type_id IN ({content_types})"
        )
        for table, column in PERMISSION_LINKS:
            if exists(table):
                cursor.execute(
                    f'DELETE FROM "{schema}"."{table}" '
                    f"WHERE {column} IN ({permissions})",
                    [APP],
                )
        cursor.execute(
            f'DELETE FROM "{schema}"."auth_permission" '
            f"WHERE content_type_id IN ({content_types})",
            [APP],
        )
    for table, column, deleted in CONTENT_TYPE_LINKS:
        if not exists(table):
            continue
        rows = f"WHERE {column} IN ({content_types})"
        if deleted:
            cursor.execute(f'DELETE FROM "{schema}"."{table}" {rows}', [APP])
        else:
            cursor.execute(
                f'UPDATE "{schema}"."{table}" SET {column} = NULL {rows}', [APP]
            )
    cursor.execute(
        f'DELETE FROM "{schema}"."django_content_type" WHERE app_label = %s',
        [APP],
    )


class Migration(migrations.Migration):
    contract_of = [("scheduler", "0001_initial")]

    dependencies = [("scheduler", "0001_initial")]

    operations = [
        migrations.RunSQL(DROP_TABLES, reverse_sql=migrations.RunSQL.noop),
        migrations.RunPython(
            forget_django_celery_beat,
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
