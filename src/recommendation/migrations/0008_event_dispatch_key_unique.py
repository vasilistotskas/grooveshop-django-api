"""One recommendation event per item per task copy.

Built concurrently on a live table (``AddUniqueConstraintAdaptively``). The
column is new and empty, so no existing row can collide, and NULLs are
outside the constraint. Duplicates, if any ever had to be checked:

    SELECT dispatch_key, count(*) FROM recommendation_recommendationevent
    WHERE dispatch_key IS NOT NULL GROUP BY dispatch_key HAVING count(*) > 1;
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ('recommendation', '0007_event_dispatch_key'),
    ]

    operations = [
        AddUniqueConstraintAdaptively(
            'recommendationevent',
            models.UniqueConstraint(
                condition=models.Q(('dispatch_key__isnull', False)),
                fields=('dispatch_key',),
                name='rec_event_dispatch_key_unique',
            ),
        ),
    ]
