# Generated by Django 5.0.3 on 2024-03-11 19:57
import django.contrib.postgres.search
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0005_product_search_document_dirty"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="product",
            name="product_search_vector_idx",
        ),
        migrations.RemoveIndex(
            model_name="product",
            name="product_search_gin",
        ),
        migrations.RemoveField(
            model_name="product",
            name="search_document",
        ),
        migrations.RemoveField(
            model_name="product",
            name="search_document_dirty",
        ),
        migrations.RemoveField(
            model_name="product",
            name="search_index_dirty",
        ),
        migrations.RemoveField(
            model_name="product",
            name="search_vector",
        ),
        migrations.AddField(
            model_name="producttranslation",
            name="search_document",
            field=models.TextField(
                blank=True,
                db_index=True,
                default="",
                verbose_name="Search Document",
            ),
        ),
        migrations.AddField(
            model_name="producttranslation",
            name="search_document_dirty",
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name="Search Document Dirty",
            ),
        ),
        migrations.AddField(
            model_name="producttranslation",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Search Vector",
            ),
        ),
        migrations.AddField(
            model_name="producttranslation",
            name="search_vector_dirty",
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name="Search Vector Dirty",
            ),
        ),
    ]
