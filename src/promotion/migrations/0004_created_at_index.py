"""Index CartPromotionCode.created_at, which every list query sorts on.

Built through ``AddIndexAdaptively``: CONCURRENTLY when the migration is
not already inside a transaction (the Argo CD PreSync ``migrate_schemas``
job, running against tenants with real rows while the old pods still
serve), and a plain ``CREATE INDEX`` when it is (tenant provisioning,
where the schema is new and the table empty). Hence ``atomic = False`` —
without it the concurrent branch can never be taken.

``ordering = ['-created_at']`` plus ``date_hierarchy`` on the same
column. updated_at is never sorted or filtered and stays
unindexed.
"""


import django.contrib.postgres.indexes
from core.db.migration_operations import AddIndexAdaptively
from django.db import migrations


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False


    dependencies = [
        ('cart', '0012_add_price_at_add_to_cartitem'),
        ('promotion', '0003_promotion_buy_quantity_and_more'),
    ]

    operations = [
        AddIndexAdaptively(
            model_name='cartpromotioncode',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['created_at'], name='cartpromotioncode_created_at_ix'),
        ),
    ]
