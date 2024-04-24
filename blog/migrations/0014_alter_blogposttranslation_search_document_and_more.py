# Generated by Django 5.0.4 on 2024-04-24 12:07

import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0013_blogposttranslation_search_document_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blogposttranslation",
            name="search_document",
            field=models.TextField(
                blank=True, default="", verbose_name="Search Document"
            ),
        ),
        migrations.AlterField(
            model_name="blogposttranslation",
            name="search_document_dirty",
            field=models.BooleanField(
                default=False, verbose_name="Search Document Dirty"
            ),
        ),
        migrations.AlterField(
            model_name="blogposttranslation",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(
                blank=True, null=True, verbose_name="Search Vector"
            ),
        ),
        migrations.AlterField(
            model_name="blogposttranslation",
            name="search_vector_dirty",
            field=models.BooleanField(
                default=False, verbose_name="Search Vector Dirty"
            ),
        ),
    ]
