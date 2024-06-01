# Generated by Django 5.0.6 on 2024-05-19 14:41
import uuid

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Contact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
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
                ("name", models.CharField(max_length=100, verbose_name="Name")),
                (
                    "email",
                    models.EmailField(
                        db_index=True, max_length=254, verbose_name="Email"
                    ),
                ),
                ("message", models.TextField(verbose_name="Message")),
            ],
            options={
                "verbose_name": "Contact",
                "verbose_name_plural": "Contacts",
                "ordering": ["-created_at"],
            },
        ),
    ]
