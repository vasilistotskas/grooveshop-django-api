from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("loyalty", "0008_points_restore_per_refund")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "loyalty.LoyaltyTierTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
