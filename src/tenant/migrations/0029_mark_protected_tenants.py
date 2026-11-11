"""Carry the protection that used to be a list of schema names in code
over to the ``is_protected`` row flag (the public schema is protected by
construction and needs no flag)."""

from django.db import migrations

PROTECTED_AT_CUTOVER = ("webside", "ekfyseosfyteias")


def mark_protected(apps, schema_editor):
    Tenant = apps.get_model("tenant", "Tenant")
    Tenant.objects.filter(schema_name__in=PROTECTED_AT_CUTOVER).update(
        is_protected=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0028_tenant_is_protected"),
    ]

    operations = [
        migrations.RunPython(mark_protected, migrations.RunPython.noop),
    ]
