"""The two marks that make a notification task safe to run more than once.

``Notification.dispatch_key`` is the dispatch that wrote the notification,
for the user it is for; ``NotificationUser.pushed_at`` is when its live push
was claimed. Both are nullable and unindexed here: the release still serving
writes neither. The unique constraint on the key is built in the next
migration, concurrently.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notification', '0020_remove_german_translations_after_rollout'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='dispatch_key',
            field=models.UUIDField(blank=True, editable=False, null=True, verbose_name='Dispatch key'),
        ),
        migrations.AddField(
            model_name='notificationuser',
            name='pushed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Pushed at'),
        ),
    ]
