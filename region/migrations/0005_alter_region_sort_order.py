# Generated by Django 5.1 on 2024-08-24 22:51
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("region", "0004_alter_region_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="region",
            name="sort_order",
            field=models.IntegerField(
                editable=False, null=True, verbose_name="Sort Order"
            ),
        ),
    ]
