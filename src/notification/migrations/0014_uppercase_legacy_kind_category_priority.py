from django.db import migrations
from django.db.models.functions import Upper


def uppercase_legacy_values(apps, schema_editor):
    """Backfill legacy lowercase values on existing Notification rows.

    Earlier versions stored ``kind`` / ``category`` / ``priority`` as
    lowercase strings (e.g. ``"info"``, ``"system"``, ``"normal"``);
    the current schema uses uppercase ``TextChoices`` (``INFO``,
    ``SYSTEM``, ``NORMAL``). The model migration that flipped the
    choices never rewrote existing rows, so the storefront's Zod
    parser rejects the response with a 422 the moment any old row
    appears in the user's notification feed (verified against prod
    notification id=21).

    Idempotent: rows already uppercased pass through unchanged via
    ``UPPER(...)`` semantics.
    """
    Notification = apps.get_model("notification", "Notification")
    Notification.objects.filter(kind__regex=r"^[a-z]+$").update(
        kind=Upper("kind")
    )
    Notification.objects.filter(category__regex=r"^[a-z]+$").update(
        category=Upper("category")
    )
    Notification.objects.filter(priority__regex=r"^[a-z]+$").update(
        priority=Upper("priority")
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("notification", "0013_alter_notification_notification_type"),
    ]

    operations = [
        migrations.RunPython(uppercase_legacy_values, noop),
    ]
