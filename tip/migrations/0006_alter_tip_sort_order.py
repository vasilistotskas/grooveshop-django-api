# Generated by Django 5.1 on 2024-08-24 22:51
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("tip", "0005_alter_tip_icon"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tip",
            name="sort_order",
            field=models.IntegerField(editable=False, null=True, verbose_name="Sort Order"),
        ),
    ]
