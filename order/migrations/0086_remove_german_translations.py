from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("order", "0085_alter_vivawebhookevent_outcome")]

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
