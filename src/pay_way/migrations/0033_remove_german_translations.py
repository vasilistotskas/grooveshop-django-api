from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("pay_way", "0032_free_threshold_help")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "pay_way.PayWayTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
