# Generated by Django 5.0.4 on 2024-04-26 11:56
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0006_alter_useraddress_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="useraccount",
            name="username",
            field=models.CharField(
                blank=True,
                max_length=30,
                null=True,
                unique=True,
                verbose_name="Username",
            ),
        ),
    ]
