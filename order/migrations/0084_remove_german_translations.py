from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("order", "0083_payment_hold_replaces_auto_cancel_hours")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "order.OrderHistoryTranslation",
                "order.OrderItemHistoryTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
