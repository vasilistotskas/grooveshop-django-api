"""Give the two BoxNow columns the same 'absent' spelling as their siblings.

Step ONE of a two-release change. A string column that allows NULL has
two spellings for "no value"; ruff's ``DJ001`` flags all thirteen in this
project. Under the Argo CD PreSync hook the ALTER that removes the NULL
spelling lands BEFORE the new image rolls, so it would run while pods
that still write NULL are serving. This release therefore only stops
PRODUCING NULL and rewrites what is already stored; the ``NOT NULL``
constraint is the next release's job.

The ``AlterField``s below emit no DDL — ``sqlmigrate`` renders each one
``(no-op)``. A Python-level ``default`` never reaches Postgres (only
``db_default`` does), so their whole effect is on ``Field.get_default()``:
without a default, a nullable ``CharField`` defaults to ``None``, which
is how every one of these columns kept collecting NULLs.

``image_url`` was built by the locker sync as
``dest.get("imageUrl") or None`` — the one field in that dict literal
spelled with None while ``title``, ``name``, ``address_line_1``,
``postal_code`` and ``note`` all used "". ``data_fingerprint`` is NULL
for an event whose envelope carries no signed-content hash, yet every
use of it in the webhook path already tests it for TRUTH, so "" reads
identically and stores unambiguously.
"""


from core.db.migration_operations import BackfillNullStringsToEmpty
from django.db import migrations, models


class Migration(migrations.Migration):
    # The backfill commits per batch; one transaction around all
    # of them would hold every row lock to the end.
    atomic = False

    dependencies = [
        ('shipping_boxnow', '0006_alter_historicalboxnowshipment_history_user'),
    ]

    operations = [
        migrations.AlterField(
            model_name='boxnowlocker',
            name='image_url',
            field=models.URLField(blank=True, default='', max_length=500, null=True, verbose_name='Image URL'),
        ),
        migrations.AlterField(
            model_name='boxnowparcelevent',
            name='data_fingerprint',
            field=models.CharField(blank=True, db_index=True, default='', help_text="SHA-256 of the HMAC-signed 'data' bytes. The signature covers only 'data', not the envelope 'id', so replay dedup keys on this content hash as well — a captured event resubmitted under a new/forged 'id' still collides here.", max_length=64, null=True, verbose_name='Signed Data Fingerprint'),
        ),
        BackfillNullStringsToEmpty(
            'boxnowlocker',
            ['image_url'],
        ),
        BackfillNullStringsToEmpty(
            'boxnowparcelevent',
            ['data_fingerprint'],
        ),
    ]
