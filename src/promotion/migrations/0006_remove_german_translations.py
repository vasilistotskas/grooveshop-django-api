from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("promotion", "0005_field_verbose_names")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "promotion.PromotionTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
