# Generated by Django 4.2 on 2023-04-13 23:50
import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("order", "0002_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="order_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
