from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """German is no longer a language of the platform: drop its rows."""

    dependencies = [("notification", "0018_field_verbose_names")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "notification.NotificationTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
