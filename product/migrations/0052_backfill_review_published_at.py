"""Give every approved review the publish date it never got.

Approving a review used to change only ``status``, so ``is_published``
stayed False and ``published_at`` empty. From this release approving
sets both, and the storefront reads the date. This stamps the reviews
approved before it.

``created_at`` is the date, not ``updated_at``. The moment of approval
was never recorded; ``updated_at`` only moves when the row is saved
(the bulk approve action never saved), so it is the customer's last edit
at best and a coin toss at worst. ``created_at`` is the date the
storefront already shows for these reviews, so nothing visible changes,
and it can never be later than the approval.

Additive and idempotent: only approved rows with no date are touched, so
a replay, or a review a newer pod approved meanwhile, is left alone.
Reviews approved by pods of the previous release while this runs stay
dateless until their next save; the admin approve action of this release
stamps them. The reverse is a no-op: a publish date is a record.
"""

from django.db import migrations
from django.db.models import F


def backfill_published_at(apps, schema_editor):
    ProductReview = apps.get_model("product", "ProductReview")
    ProductReview.objects.filter(
        status="TRUE", published_at__isnull=True
    ).update(published_at=F("created_at"), is_published=True)


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0051_reprice_discounted_products"),
    ]

    operations = [
        migrations.RunPython(backfill_published_at, migrations.RunPython.noop),
    ]
