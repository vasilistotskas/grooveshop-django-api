# Generated by Django 4.2.9 on 2024-02-10 14:39
import uuid

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
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated At"),
                ),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "value",
                    models.DecimalField(decimal_places=1, max_digits=11, verbose_name="Value"),
                ),
            ],
            options={
                "verbose_name": "Vat",
                "verbose_name_plural": "Vats",
                "ordering": ["-created_at"],
            },
        ),
    ]
