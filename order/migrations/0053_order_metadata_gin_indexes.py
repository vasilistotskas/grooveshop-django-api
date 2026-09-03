"""Give ``order_order.metadata`` the GIN index its model always declared.

``MetaDataModel`` declares a GIN index on each of its two jsonb columns,
but ``Order.Meta`` defines its own ``indexes`` list, which REPLACES the
abstract parents' rather than extending it. It splatted
``TimeStampMixinModel.Meta.indexes`` and not ``MetaDataModel``'s, so the
pair was silently absent — ``Product``, which splats both, has had them
all along.

The cost fell on the Viva webhook. Every delivery resolves the order
with ``metadata__contains={"viva_order_codes": [code]}`` (jsonb ``@>``),
and 1796/1797/1798 all take that path; Viva retries an unacknowledged
event hourly, 23 times. Without the index each of those is a sequential
scan of the whole orders table. ``metadata__has_key`` in the B2B admin
filter and the amount-mismatch sweep in ``order/tasks.py`` scan the same
way.

Built CONCURRENTLY, which is why this migration is ``atomic = False``:
Postgres refuses ``CREATE INDEX CONCURRENTLY`` inside a transaction
block. A plain build takes a SHARE lock on ``order_order``, which blocks
every INSERT and UPDATE for its duration — and under the Argo CD PreSync
hook these run BEFORE the new image rolls out, so the store is live and
taking checkouts throughout. Concurrent builds take twice as long and
need two table scans; they do not block writes.

If a concurrent build fails (a deadlock, a cancelled deploy), Postgres
leaves the index behind marked INVALID. It is not used by queries and
not repaired automatically: drop it and re-run.

    SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
    DROP INDEX CONCURRENTLY <name>;
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    atomic = False

    dependencies = [
        ("country", "0010_seed_default_country"),
        ("order", "0052_fold_viva_order_code_into_history"),
        ("pay_way", "0019_seed_default_pay_ways"),
        ("region", "0009_seed_default_regions"),
        ("shipping", "0008_shippingprovider_logo_pickup_point_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=GinIndex(fields=["metadata"], name="order_meta_ix"),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=GinIndex(fields=["private_metadata"], name="order_p_meta_ix"),
        ),
    ]
