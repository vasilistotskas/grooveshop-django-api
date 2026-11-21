"""The key that makes two recommendation event rows one event.

Nullable and unindexed here: the release still serving writes none, and
``attach`` rows never carry one. The unique constraint on it is built in the
next migration, concurrently.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recommendation', '0006_slot_learn_weights_and_built_strategies'),
    ]

    operations = [
        migrations.AddField(
            model_name='recommendationevent',
            name='event_key',
            field=models.UUIDField(blank=True, editable=False, null=True, verbose_name='Event key'),
        ),
    ]
