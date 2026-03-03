from django.db import migrations


def seed_webside_tenant(apps, schema_editor):
    schema_editor.execute("CREATE SCHEMA IF NOT EXISTS webside")

    Tenant = apps.get_model("tenant", "Tenant")
    TenantDomain = apps.get_model("tenant", "TenantDomain")

    tenant, _ = Tenant.objects.get_or_create(
        schema_name="webside",
        defaults={
            "name": "Webside",
            "slug": "webside",
            "owner_email": "admin@webside.gr",
            "is_active": True,
            "plan": "enterprise",
            "store_name": "Webside",
            "blog_enabled": True,
            "loyalty_enabled": True,
        },
    )

    for domain, is_primary in [
        ("webside.gr", True),
        ("api.webside.gr", False),
        ("www.webside.gr", False),
    ]:
        TenantDomain.objects.get_or_create(
            domain=domain,
            defaults={"tenant": tenant, "is_primary": is_primary},
        )


def remove_webside_tenant(apps, schema_editor):
    TenantDomain = apps.get_model("tenant", "TenantDomain")
    Tenant = apps.get_model("tenant", "Tenant")

    TenantDomain.objects.filter(
        domain__in=["webside.gr", "api.webside.gr", "www.webside.gr"]
    ).delete()
    Tenant.objects.filter(schema_name="webside", slug="webside").delete()

    schema_editor.execute("DROP SCHEMA IF EXISTS webside CASCADE")


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_webside_tenant,
            remove_webside_tenant,
        ),
    ]
