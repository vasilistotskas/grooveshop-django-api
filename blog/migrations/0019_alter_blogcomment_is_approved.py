# Generated by Django 5.0.8 on 2024-08-19 17:08
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0018_alter_blogcomment_is_approved"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blogcomment",
            name="is_approved",
            field=models.BooleanField(default=False, verbose_name="Is Approved"),
        ),
    ]
