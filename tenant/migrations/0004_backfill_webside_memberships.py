"""Backfill a webside membership for every existing user.

The ``UserTenantMembership`` model landed in 0003 — without this
migration, existing webside.gr users would still have a ``UserAccount``
row in the public schema but no membership, which the new permission
checks would read as "not allowed to access this tenant" and every
login would fail.

Superusers + staff get OWNER so they keep full access; everyone else
gets MEMBER. New tenants get memberships via ``create_tenant`` /
the tenant admin — this migration only rescues the pre-existing data.
"""

from django.db import migrations


def create_webside_memberships(apps, schema_editor):
    Tenant = apps.get_model("tenant", "Tenant")
    UserTenantMembership = apps.get_model("tenant", "UserTenantMembership")
    User = apps.get_model("user", "UserAccount")

    webside = Tenant.objects.filter(schema_name="webside").first()
    if webside is None:
        # Non-webside environments (CI, fresh-install dev): nothing to
        # backfill. New tenants provision their own memberships.
        return

    existing_user_ids = set(
        UserTenantMembership.objects.filter(tenant=webside).values_list(
            "user_id", flat=True
        )
    )

    to_create = []
    for user in User.objects.all().only("id", "is_staff", "is_superuser"):
        if user.id in existing_user_ids:
            continue
        if user.is_superuser or user.is_staff:
            role = "owner"
        else:
            role = "member"
        to_create.append(
            UserTenantMembership(
                user=user,
                tenant=webside,
                role=role,
                is_active=True,
            )
        )

    if to_create:
        UserTenantMembership.objects.bulk_create(
            to_create, batch_size=500, ignore_conflicts=True
        )


def drop_webside_memberships(apps, schema_editor):
    UserTenantMembership = apps.get_model("tenant", "UserTenantMembership")
    Tenant = apps.get_model("tenant", "Tenant")
    webside = Tenant.objects.filter(schema_name="webside").first()
    if webside is None:
        return
    UserTenantMembership.objects.filter(tenant=webside).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tenant", "0003_add_user_tenant_membership"),
    ]

    operations = [
        migrations.RunPython(
            create_webside_memberships,
            drop_webside_memberships,
        ),
    ]
