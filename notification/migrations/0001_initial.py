# Generated by Django 4.2.9 on 2024-02-06 11:34
import uuid

import django.db.models.deletion
import parler.models
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Notification",
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
                ("link", models.URLField(blank=True, null=True, verbose_name="Link")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("error", "Error"),
                            ("success", "Success"),
                            ("info", "Info"),
                            ("warning", "Warning"),
                            ("danger", "Danger"),
                        ],
                        default="info",
                        max_length=250,
                        verbose_name="Kind",
                    ),
                ),
            ],
            options={
                "verbose_name": "Notification",
                "verbose_name_plural": "Notifications",
                "ordering": ["-created_at"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="NotificationTranslation",
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
                    "language_code",
                    models.CharField(
                        db_index=True, max_length=15, verbose_name="Language"
                    ),
                ),
                ("title", models.CharField(max_length=250, verbose_name="Title")),
                ("message", models.TextField(verbose_name="Message")),
            ],
            options={
                "verbose_name": "Notification Translation",
                "db_table": "notification_notification_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="NotificationUser",
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
                ("seen", models.BooleanField(default=False, verbose_name="Seen")),
                (
                    "seen_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Seen At"),
                ),
                (
                    "notification",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="notification.notification",
                    ),
                ),
            ],
            options={
                "verbose_name": "Notification User",
                "verbose_name_plural": "Notification Users",
                "ordering": ["-notification__created_at"],
            },
        ),
    ]
