# Generated by Django 5.0.3 on 2024-03-31 12:31
import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="payway",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="payway_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payway",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="payway_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payway",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["sort_order"], name="payway_sort_order_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payway",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["active"], name="pay_way_pay_active_1c54dd_btree"
            ),
        ),
    ]
