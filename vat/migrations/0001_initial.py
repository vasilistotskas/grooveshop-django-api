# Generated by Django 4.2.5 on 2023-09-23 12:04
import uuid

import django.utils.timezone
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Vat",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="Created At"
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "value",
                    models.DecimalField(
                        decimal_places=1, max_digits=11, verbose_name="Value"
                    ),
                ),
            ],
            options={
                "verbose_name": "Vat",
                "verbose_name_plural": "Vats",
                "ordering": ["-created_at"],
            },
        ),
    ]
