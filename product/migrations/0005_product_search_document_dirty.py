# Generated by Django 5.0.3 on 2024-03-09 20:14
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        (
            "product",
            "0004_product_search_document_product_search_index_dirty_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="search_document_dirty",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
