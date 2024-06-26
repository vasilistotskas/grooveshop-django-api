# Generated by Django 5.0.3 on 2024-04-01 08:29
import django.contrib.postgres.indexes
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0008_product_product_created_at_idx_and_more"),
        ("vat", "0003_vat_vat_vat_value_d54579_btree"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="product",
            name="product_pro_hits_19ae26_btree",
        ),
        migrations.RemoveField(
            model_name="product",
            name="hits",
        ),
        migrations.AddField(
            model_name="product",
            name="view_count",
            field=models.PositiveBigIntegerField(default=0, verbose_name="View Count"),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["view_count"], name="product_pro_view_co_bf71fd_btree"
            ),
        ),
    ]
