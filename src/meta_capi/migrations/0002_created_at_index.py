"""Give the CAPI event log an index its ORDER BY can use.

Built through ``AddIndexAdaptively``: CONCURRENTLY when the migration is
not already inside a transaction (the Argo CD PreSync ``migrate_schemas``
job, running against tenants with real rows while the old pods still
serve), and a plain ``CREATE INDEX`` when it is (tenant provisioning,
where the schema is new and the table empty). Hence ``atomic = False`` —
without it the concurrent branch can never be taken.

The table already carries ``(event_name, -created_at)`` and
``(status, -created_at)``, but both LEAD with another column, so
Postgres can use neither for the admin's plain
``ORDER BY -created_at`` or for ``date_hierarchy``. This is the
fastest-growing table of the group — one row per dispatch attempt.
"""


from django.conf import settings
from core.db.migration_operations import AddIndexAdaptively
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False


    dependencies = [
        ('meta_capi', '0001_initial'),
        ('order', '0053_order_metadata_gin_indexes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexAdaptively(
            model_name='metacapieventlog',
            index=models.Index(fields=['-created_at'], name='meta_capi_m_created_cc3ee3_idx'),
        ),
    ]
