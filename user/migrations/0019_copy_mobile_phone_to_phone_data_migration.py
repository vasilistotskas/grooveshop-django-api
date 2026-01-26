# Generated manually on 2026-01-24
# Data migration to copy mobile_phone values to phone field before removal

from django.db import migrations


def copy_mobile_phone_to_phone(apps, schema_editor):
    """
    Copy mobile_phone values to phone field where phone is NULL.
    This ensures no data is lost when we remove the mobile_phone field.
    """
    UserAddress = apps.get_model('user', 'UserAddress')

    # Update addresses where phone is NULL but mobile_phone has a value
    addresses_to_update = UserAddress.objects.filter(
        phone__isnull=True,
        mobile_phone__isnull=False
    )

    count = 0
    for address in addresses_to_update:
        address.phone = address.mobile_phone
        address.save(update_fields=['phone'])
        count += 1

    if count > 0:
        print(f"✓ Copied mobile_phone to phone for {count} UserAddress records")


def reverse_copy(apps, schema_editor):
    """
    Reverse migration - this is informational only.
    Cannot truly reverse as we don't know which records originally had NULL phone.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0018_alter_useraccount_username'),
    ]

    operations = [
        migrations.RunPython(copy_mobile_phone_to_phone, reverse_copy),
    ]
