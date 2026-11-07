import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """``Product.vat`` is required and its rate is protected from deletion.

    Release 1 of two. State only: no column changes, so the release still
    serving during the rollout is unaffected. The column stays nullable until
    the next release makes it NOT NULL.
    """

    dependencies = [
        ("product", "0068_backfill_missing_vat"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="vat",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="vat.vat",
                verbose_name="VAT",
            ),
        ),
    ]
