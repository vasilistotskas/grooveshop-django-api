# Generated by Django 5.0.3 on 2024-04-01 08:29
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0005_blogauthor_blogauthor_created_at_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blogpost",
            name="view_count",
            field=models.PositiveBigIntegerField(default=0, verbose_name="View Count"),
        ),
    ]
