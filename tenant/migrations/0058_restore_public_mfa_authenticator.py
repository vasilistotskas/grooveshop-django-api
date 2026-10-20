from django.db import migrations


def create_missing_authenticator_table(apps, schema_editor):
    """Create ``mfa_authenticator`` where it is recorded but absent.

    A database that predates multi-tenancy recorded the ``mfa``
    migrations in the public schema while the table lived there; the
    cutover cleanup then dropped the table and left the records. Once
    ``allauth.mfa`` was added to ``SHARED_APPS`` for the platform staff
    second factor, ``migrate`` saw nothing to do, and every superuser
    admin login failed on the missing table. Created from the migrated
    model state, so it is exactly what the ``mfa`` migrations build; a
    schema that already has the table (every fresh database, every
    tenant schema) is left alone.
    """
    authenticator = apps.get_model("mfa", "Authenticator")
    table = authenticator._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        existing = schema_editor.connection.introspection.table_names(cursor)
    if table not in existing:
        schema_editor.create_model(authenticator)


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0057_retire_member_role"),
        ("mfa", "0003_authenticator_type_uniq"),
    ]

    operations = [
        migrations.RunPython(
            create_missing_authenticator_table, migrations.RunPython.noop
        ),
    ]
