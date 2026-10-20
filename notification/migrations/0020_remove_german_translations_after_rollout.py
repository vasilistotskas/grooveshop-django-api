from django.db import migrations

from core.utils.i18n import delete_translations_in


class Migration(migrations.Migration):
    """Repeat 0019 once the release before it has stopped serving.

    That release still listed German, and its notification service saves a
    translation for every configured language, so a notification sent
    during the rollout could write a ``de`` row after 0019 had run.
    """

    dependencies = [("notification", "0019_remove_german_translations")]

    operations = [
        migrations.RunPython(
            delete_translations_in(
                "de",
                "notification.NotificationTranslation",
            ),
            migrations.RunPython.noop,
        ),
    ]
