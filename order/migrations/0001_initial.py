# Generated by Django 4.2.9 on 2024-02-10 14:39
import uuid
from decimal import Decimal

import django.db.models.deletion
import djmoney.models.fields
import phonenumber_field.modelfields
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Order",
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
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "floor",
                    models.CharField(
                        blank=True,
                        choices=[
                            (0, "Basement"),
                            (1, "Ground Floor"),
                            (2, "First Floor"),
                            (3, "Second Floor"),
                            (4, "Third Floor"),
                            (5, "Fourth Floor"),
                            (6, "Fifth Floor"),
                            (7, "Sixth Floor Plus"),
                        ],
                        default=None,
                        max_length=50,
                        null=True,
                    ),
                ),
                (
                    "location_type",
                    models.CharField(
                        blank=True,
                        choices=[(0, "Home"), (1, "Office"), (2, "Other")],
                        default=None,
                        max_length=100,
                        null=True,
                    ),
                ),
                ("email", models.EmailField(max_length=255, verbose_name="Email")),
                (
                    "first_name",
                    models.CharField(max_length=255, verbose_name="First Name"),
                ),
                (
                    "last_name",
                    models.CharField(max_length=255, verbose_name="Last Name"),
                ),
                ("street", models.CharField(max_length=255, verbose_name="Street")),
                (
                    "street_number",
                    models.CharField(max_length=255, verbose_name="Street Number"),
                ),
                ("city", models.CharField(max_length=255, verbose_name="City")),
                ("zipcode", models.CharField(max_length=255, verbose_name="Zipcode")),
                (
                    "place",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Place"
                    ),
                ),
                (
                    "phone",
                    phonenumber_field.modelfields.PhoneNumberField(
                        max_length=128, region=None, verbose_name="Phone Number"
                    ),
                ),
                (
                    "mobile_phone",
                    phonenumber_field.modelfields.PhoneNumberField(
                        blank=True,
                        default=None,
                        max_length=128,
                        null=True,
                        region=None,
                        verbose_name="Mobile Phone Number",
                    ),
                ),
                (
                    "customer_notes",
                    models.TextField(
                        blank=True, null=True, verbose_name="Customer Notes"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SENT", "Sent"),
                            ("PAID_AND_SENT", "Paid and Sent"),
                            ("CANCELED", "Canceled"),
                            ("PENDING", "Pending"),
                        ],
                        default="PENDING",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "shipping_price_currency",
                    djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
                (
                    "shipping_price",
                    djmoney.models.fields.MoneyField(
                        decimal_places=2,
                        default=Decimal("0"),
                        max_digits=11,
                        verbose_name="Shipping Price",
                    ),
                ),
                (
                    "document_type",
                    models.CharField(
                        choices=[("RECEIPT", "Receipt"), ("INVOICE", "Invoice")],
                        default="RECEIPT",
                        max_length=100,
                        verbose_name="Document Type",
                    ),
                ),
                (
                    "paid_amount_currency",
                    djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                        null=True,
                    ),
                ),
                (
                    "paid_amount",
                    djmoney.models.fields.MoneyField(
                        decimal_places=2,
                        default=Decimal("0"),
                        max_digits=11,
                        null=True,
                        verbose_name="Paid Amount",
                    ),
                ),
            ],
            options={
                "verbose_name": "Order",
                "verbose_name_plural": "Orders",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
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
                    "price_currency",
                    djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
                (
                    "price",
                    djmoney.models.fields.MoneyField(
                        decimal_places=2, max_digits=11, verbose_name="Price"
                    ),
                ),
                ("quantity", models.IntegerField(default=1, verbose_name="Quantity")),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="order_item_order",
                        to="order.order",
                    ),
                ),
            ],
            options={
                "verbose_name": "Order Item",
                "verbose_name_plural": "Order Items",
                "ordering": ["sort_order"],
            },
        ),
    ]
