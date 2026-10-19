from django.db import migrations, models


def member_rows_to_inactive_staff(apps, schema_editor):
    """Give every leftover MEMBER row an explicit role.

    MEMBER was retired: it granted no access, and rows were still being
    created by the model's default. STAFF is the least role that means
    anything, and the row is switched off so the migration hands nobody
    access they did not have. A store owner who meant the person to have
    it turns the row back on.
    """
    membership = apps.get_model("tenant", "UserTenantMembership")
    membership.objects.filter(role="member").update(
        role="staff", is_active=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0056_remove_check_pending_orders_beat"),
    ]

    operations = [
        migrations.RunPython(
            member_rows_to_inactive_staff, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="usertenantmembership",
            name="role",
            field=models.CharField(
                choices=[
                    ("staff", "Staff"),
                    ("admin", "Admin"),
                    ("owner", "Owner"),
                ],
                max_length=20,
                verbose_name="Role",
            ),
        ),
    ]
