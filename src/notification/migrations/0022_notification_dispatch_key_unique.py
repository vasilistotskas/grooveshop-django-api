"""One notification per dispatch key.

Built concurrently on a live table (``AddUniqueConstraintAdaptively``). The
column is new and empty, so no existing row can collide; the release still
serving writes no key, and NULLs are outside the constraint. Duplicates, if
any ever had to be checked:

    SELECT dispatch_key, count(*) FROM notification_notification
    WHERE dispatch_key IS NOT NULL GROUP BY dispatch_key HAVING count(*) > 1;
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ('notification', '0021_dispatch_key_pushed_at'),
    ]

    operations = [
        AddUniqueConstraintAdaptively(
            'notification',
            models.UniqueConstraint(
                condition=models.Q(('dispatch_key__isnull', False)),
                fields=('dispatch_key',),
                name='notification_dispatch_key_unique',
            ),
        ),
    ]
