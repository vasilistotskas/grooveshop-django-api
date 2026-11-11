from django.db import migrations


def drop_member_rows(apps, schema_editor):
    """Delete MEMBER-role memberships — customers no longer hold any.

    Membership is a STAFF grant over a tenant, held by platform-public
    identities. Customers are scoped to a store by living in that
    store's schema, not by a row in this table (see
    ``tenant/membership.py``).

    The MEMBER rows this removes were backfilled by 0004 for every
    pre-cutover shopper and are now inert — but not harmless: they are
    keyed by a PUBLIC user id, while a request on a tenant host
    authenticates against that tenant's OWN user table where the same id
    belongs to a different person. Any lookup by id (the memberships
    listing, a future gate) could match the wrong human. Deleting them
    leaves a table whose every row means what its name says.

    OWNER / ADMIN / STAFF rows are untouched.
    """
    UserTenantMembership = apps.get_model("tenant", "UserTenantMembership")
    UserTenantMembership.objects.filter(role="member").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0014_remove_stripe_platform_account_and_turnstile"),
    ]

    operations = [
        # Deliberately irreversible: re-creating shopper memberships would
        # re-introduce rows that cannot be correct (a tenant-schema user
        # has no public id to key them by).
        migrations.RunPython(drop_member_rows, migrations.RunPython.noop),
    ]
