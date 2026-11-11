"""The scheduler's state, starting from what django-celery-beat knew.

The copy keeps a job an operator switched off switched off, and gives
``emails.gates.ScheduleGate`` its rows before the new beat first starts. The old
tables stay until the next release: the previous image still reads them while
it serves, and a rollback needs them.
"""

from django.db import migrations, models

COPY_FROM_DJANGO_CELERY_BEAT = """
DO $$
BEGIN
    IF to_regclass('django_celery_beat_periodictask') IS NOT NULL THEN
        INSERT INTO scheduler_periodictask
            (name, enabled, last_run_at, total_run_count)
        SELECT name, enabled, last_run_at, total_run_count
        FROM django_celery_beat_periodictask
        ON CONFLICT (name) DO NOTHING;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PeriodicTask",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=200, unique=True, verbose_name="Name"
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        db_default=True, default=True, verbose_name="Enabled"
                    ),
                ),
                (
                    "resumed_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Switched on at"
                    ),
                ),
                (
                    "last_run_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Last run"
                    ),
                ),
                (
                    "total_run_count",
                    models.PositiveBigIntegerField(
                        db_default=0, default=0, verbose_name="Runs"
                    ),
                ),
            ],
            options={
                "verbose_name": "Periodic Task",
                "verbose_name_plural": "Periodic Tasks",
                "ordering": ("name",),
            },
        ),
        migrations.RunSQL(
            COPY_FROM_DJANGO_CELERY_BEAT, reverse_sql=migrations.RunSQL.noop
        ),
    ]
