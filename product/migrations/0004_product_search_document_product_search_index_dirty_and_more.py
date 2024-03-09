# Generated by Django 5.0.3 on 2024-03-09 14:54

import django.contrib.postgres.indexes
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("product", "0003_alter_product_weight"),
        ("vat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="search_document",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="product",
            name="search_index_dirty",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_document"],
                name="product_search_gin",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
