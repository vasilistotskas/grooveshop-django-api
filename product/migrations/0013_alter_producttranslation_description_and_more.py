# Generated by Django 5.0.4 on 2024-04-24 12:07
import django.contrib.postgres.search
import tinymce.models
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0012_alter_product_discount_percent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="producttranslation",
            name="description",
            field=tinymce.models.HTMLField(
                blank=True, null=True, verbose_name="Description"
            ),
        ),
        migrations.AlterField(
            model_name="producttranslation",
            name="name",
            field=models.CharField(
                blank=True, max_length=255, null=True, verbose_name="Name"
            ),
        ),
        migrations.AlterField(
            model_name="producttranslation",
            name="search_document",
            field=models.TextField(
                blank=True, default="", verbose_name="Search Document"
            ),
        ),
        migrations.AlterField(
            model_name="producttranslation",
            name="search_document_dirty",
            field=models.BooleanField(
                default=False, verbose_name="Search Document Dirty"
            ),
        ),
        migrations.AlterField(
            model_name="producttranslation",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                blank=True, null=True, verbose_name="Search Vector"
            ),
        ),
        migrations.AlterField(
            model_name="producttranslation",
            name="search_vector_dirty",
            field=models.BooleanField(
                default=False, verbose_name="Search Vector Dirty"
            ),
        ),
    ]
