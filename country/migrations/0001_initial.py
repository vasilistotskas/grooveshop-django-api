# Generated by Django 4.2.9 on 2024-02-06 11:34
import uuid

import django.core.validators
import django.db.models.deletion
import parler.fields
import parler.models
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Country",
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
                (
                    "alpha_2",
                    models.CharField(
                        db_index=True,
                        max_length=2,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Enter a valid 2-letter country code.",
                                regex="^[A-Z]{2}$",
                            )
                        ],
                        verbose_name="Country Code Alpha 2",
                    ),
                ),
                (
                    "alpha_3",
                    models.CharField(
                        db_index=True,
                        max_length=3,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Enter a valid 3-letter country code.",
                                regex="^[A-Z]{3}$",
                            )
                        ],
                        verbose_name="Country Code Alpha 3",
                    ),
                ),
                (
                    "iso_cc",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                        unique=True,
                        verbose_name="ISO Country Code",
                    ),
                ),
                (
                    "phone_code",
                    models.PositiveSmallIntegerField(
                        blank=True, null=True, unique=True, verbose_name="Phone Code"
                    ),
                ),
                (
                    "image_flag",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/country/",
                        verbose_name="Image Flag",
                    ),
                ),
            ],
            options={
                "verbose_name": "Country",
                "verbose_name_plural": "Countries",
                "ordering": ["sort_order"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="CountryTranslation",
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
                    "name",
                    models.CharField(
                        blank=True, max_length=100, null=True, verbose_name="Name"
                    ),
                ),
                (
                    "master",
                    parler.fields.TranslationsForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="country.country",
                    ),
                ),
            ],
            options={
                "verbose_name": "Country Translation",
                "db_table": "country_country_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
                "unique_together": {("language_code", "master")},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
    ]
