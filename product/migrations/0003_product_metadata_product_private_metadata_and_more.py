# Generated by Django 4.2.6 on 2023-11-02 10:56

import django.contrib.postgres.indexes
import django.core.serializers.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                encoder=django.core.serializers.json.DjangoJSONEncoder,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="private_metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                encoder=django.core.serializers.json.DjangoJSONEncoder,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["private_metadata"], name="product_p_meta_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["metadata"], name="product_meta_idx"
            ),
        ),
    ]
