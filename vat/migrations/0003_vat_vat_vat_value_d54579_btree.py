# Generated by Django 5.0.3 on 2024-03-31 13:31
import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("vat", "0002_vat_vat_created_at_idx_vat_vat_updated_at_idx"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="vat",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["value"], name="vat_vat_value_d54579_btree"
            ),
        ),
    ]
