# Generated by Django 4.2.9 on 2024-02-06 11:34
import uuid

import django.db.models.deletion
import parler.fields
import parler.models
import tinymce.models
from django.db import migrations
from django.db import models

import tip.validators


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Tip",
            fields=[
                (
                    "sort_order",
                    models.IntegerField(
                        db_index=True,
                        editable=False,
                        null=True,
                        verbose_name="Sort Order",
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
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("SUCCESS", "Success"),
                            ("INFO", "Info"),
                            ("ERROR", "Error"),
                            ("WARNING", "Warning"),
                        ],
                        max_length=25,
                        verbose_name="Kind",
                    ),
                ),
                (
                    "icon",
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to="uploads/tip/",
                        validators=[tip.validators.validate_file_extension],
                        verbose_name="Icon",
                    ),
                ),
                ("active", models.BooleanField(default=True, verbose_name="Active")),
            ],
            options={
                "verbose_name": "Tip",
                "verbose_name_plural": "Tips",
                "ordering": ["sort_order"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="TipTranslation",
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
                (
                    "title",
                    models.CharField(
                        blank=True, max_length=200, null=True, verbose_name="Title"
                    ),
                ),
                (
                    "content",
                    tinymce.models.HTMLField(
                        blank=True, null=True, verbose_name="Content"
                    ),
                ),
                (
                    "url",
                    models.URLField(
                        blank=True, max_length=255, null=True, verbose_name="Url"
                    ),
                ),
                (
                    "master",
                    parler.fields.TranslationsForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="tip.tip",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tip Translation",
                "db_table": "tip_tip_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
                "unique_together": {("language_code", "master")},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
    ]
