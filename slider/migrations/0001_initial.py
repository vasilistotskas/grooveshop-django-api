# Generated by Django 4.2.5 on 2023-09-17 01:46
import uuid

import django.db.models.deletion
import django.utils.timezone
import parler.fields
import parler.models
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Slider",
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
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/sliders/",
                        verbose_name="Image",
                    ),
                ),
                (
                    "thumbnail",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/sliders/thumbnails/",
                        verbose_name="Thumbnail",
                    ),
                ),
                (
                    "video",
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to="uploads/sliders/videos/",
                        verbose_name="Video",
                    ),
                ),
            ],
            options={
                "verbose_name": "Slider",
                "verbose_name_plural": "Sliders",
                "ordering": ["-created_at"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Slide",
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
                    "discount",
                    models.DecimalField(
                        decimal_places=2,
                        default=0.0,
                        max_digits=11,
                        verbose_name="Discount",
                    ),
                ),
                (
                    "show_button",
                    models.BooleanField(default=False, verbose_name="Show Button"),
                ),
                ("date_start", models.DateTimeField(verbose_name="Date Start")),
                ("date_end", models.DateTimeField(verbose_name="Date End")),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/slides/",
                        verbose_name="Image",
                    ),
                ),
                (
                    "thumbnail",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/slides/thumbnails/",
                        verbose_name="Thumbnail",
                    ),
                ),
                (
                    "slider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="slide_slider",
                        to="slider.slider",
                    ),
                ),
            ],
            options={
                "verbose_name": "Slide",
                "verbose_name_plural": "Slides",
                "ordering": ["sort_order"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="SlideTranslation",
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
                        blank=True, max_length=50, null=True, verbose_name="Name"
                    ),
                ),
                (
                    "url",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Url"
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True, max_length=40, null=True, verbose_name="Title"
                    ),
                ),
                (
                    "subtitle",
                    models.CharField(
                        blank=True, max_length=40, null=True, verbose_name="Subtitle"
                    ),
                ),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Description",
                    ),
                ),
                (
                    "button_label",
                    models.CharField(
                        blank=True,
                        max_length=25,
                        null=True,
                        verbose_name="Button Label",
                    ),
                ),
                (
                    "master",
                    parler.fields.TranslationsForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="slider.slide",
                    ),
                ),
            ],
            options={
                "verbose_name": "Slide Translation",
                "db_table": "slider_slide_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
                "unique_together": {("language_code", "master")},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="SliderTranslation",
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
                        blank=True, max_length=50, null=True, verbose_name="Name"
                    ),
                ),
                (
                    "url",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Url"
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True, max_length=40, null=True, verbose_name="Title"
                    ),
                ),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        null=True,
                        verbose_name="Description",
                    ),
                ),
                (
                    "master",
                    parler.fields.TranslationsForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="slider.slider",
                    ),
                ),
            ],
            options={
                "verbose_name": "Slider Translation",
                "db_table": "slider_slider_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
                "unique_together": {("language_code", "master")},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
    ]
