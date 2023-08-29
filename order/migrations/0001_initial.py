# Generated by Django 4.2.4 on 2023-08-29 09:51
import uuid

import django.db.models.deletion
import django.utils.timezone
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
                    "floor",
                    models.CharField(
                        blank=True,
                        choices=[
                            (0, "BASEMENT"),
                            (1, "GROUND_FLOOR"),
                            (2, "FIRST_FLOOR"),
                            (3, "SECOND_FLOOR"),
                            (4, "THIRD_FLOOR"),
                            (5, "FOURTH_FLOOR"),
                            (6, "FIFTH_FLOOR"),
                            (7, "SIXTH_FLOOR_PLUS"),
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
                        choices=[(0, "HOME"), (1, "OFFICE"), (2, "OTHER")],
                        default=None,
                        max_length=100,
                        null=True,
                    ),
                ),
                ("email", models.CharField(max_length=255, verbose_name="Email")),
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
                ("phone", models.CharField(max_length=255, verbose_name="Phone")),
                (
                    "mobile_phone",
                    models.CharField(
                        blank=True,
                        default=None,
                        max_length=255,
                        null=True,
                        verbose_name="Mobile Phone",
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
                            ("Sent", "SENT"),
                            ("Paid and sent", "PAID_AND_SENT"),
                            ("Canceled", "CANCELED"),
                            ("Pending", "PENDING"),
                        ],
                        default="Pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "shipping_price",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=8,
                        verbose_name="Shipping Price",
                    ),
                ),
                (
                    "document_type",
                    models.CharField(
                        choices=[("receipt", "RECEIPT"), ("invoice", "INVOICE")],
                        default="receipt",
                        max_length=10,
                        verbose_name="Document Type",
                    ),
                ),
                (
                    "paid_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        default=0,
                        max_digits=8,
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
                    "price",
                    models.DecimalField(
                        decimal_places=2, max_digits=8, verbose_name="Price"
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
