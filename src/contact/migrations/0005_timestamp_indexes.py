"""Index the timestamps the contact admin actually reads.

Built through ``AddIndexAdaptively``: CONCURRENTLY when the migration is
not already inside a transaction (the Argo CD PreSync ``migrate_schemas``
job, running against tenants with real rows while the old pods still
serve), and a plain ``CREATE INDEX`` when it is (tenant provisioning,
where the schema is new and the table empty). Hence ``atomic = False`` —
without it the concurrent branch can never be taken.

Contact orders its list by created_at and paginates it, ``date_hierarchy`` range-scans that column, RecentContactFilter
issues four ``created_at__gte`` variants, and the admin exposes a
RangeDateTimeFilter on updated_at as well — so Contact earns both
halves of TimeStampMixinModel's pair. Feedback earns only
created_at: its updated_at is displayed but never sorted or
filtered, and an index nobody reads still costs a write per row.
"""


import django.contrib.postgres.indexes
from core.db.migration_operations import AddIndexAdaptively
from django.db import migrations


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False


    dependencies = [
        ('contact', '0004_feedback'),
    ]

    operations = [
        AddIndexAdaptively(
            model_name='contact',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['created_at'], name='contact_created_at_ix'),
        ),
        AddIndexAdaptively(
            model_name='contact',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['updated_at'], name='contact_updated_at_ix'),
        ),
        AddIndexAdaptively(
            model_name='feedback',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['created_at'], name='feedback_created_at_ix'),
        ),
    ]
