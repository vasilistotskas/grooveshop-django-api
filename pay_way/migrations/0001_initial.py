# Generated by Django 4.2.9 on 2024-02-10 14:39
import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import djmoney.models.fields
import parler.fields
import parler.models
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PayWay",
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
                    "active",
                    models.BooleanField(default=True, verbose_name="Active"),
                ),
                (
                    "cost_currency",
                    djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
                (
                    "cost",
                    djmoney.models.fields.MoneyField(
                        decimal_places=2,
                        default=Decimal("0"),
                        max_digits=11,
                        validators=[django.core.validators.MinValueValidator(0)],
                        verbose_name="Cost",
                    ),
                ),
                (
                    "free_for_order_amount_currency",
                    djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
                (
                    "free_for_order_amount",
                    djmoney.models.fields.MoneyField(
                        decimal_places=2,
                        default=Decimal("0"),
                        max_digits=11,
                        validators=[django.core.validators.MinValueValidator(0)],
                        verbose_name="Free For Order Amount",
                    ),
                ),
                (
                    "icon",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/pay_way/",
                        verbose_name="Icon",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pay Way",
                "verbose_name_plural": "Pay Ways",
                "ordering": ["sort_order"],
            },
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name="PayWayTranslation",
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
                        blank=True,
                        choices=[
                            ("CREDIT_CARD", "Credit Card"),
                            ("PAY_ON_DELIVERY", "Pay On Delivery"),
                            ("PAY_ON_STORE", "Pay On Store"),
                            ("PAY_PAL", "PayPal"),
                        ],
                        max_length=50,
                        null=True,
                        verbose_name="Name",
                    ),
                ),
                (
                    "master",
                    parler.fields.TranslationsForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="pay_way.payway",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pay Way Translation",
                "db_table": "pay_way_payway_translation",
                "db_tablespace": "",
                "managed": True,
                "default_permissions": (),
                "unique_together": {("language_code", "master")},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
    ]
