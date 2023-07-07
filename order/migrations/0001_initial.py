# Generated by Django 4.2 on 2023-04-13 23:50

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


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
                ("id", models.AutoField(primary_key=True, serialize=False)),
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
                ("email", models.CharField(max_length=100)),
                ("first_name", models.CharField(max_length=100)),
                ("last_name", models.CharField(max_length=100)),
                ("street", models.CharField(max_length=100)),
                ("street_number", models.CharField(max_length=100)),
                ("city", models.CharField(max_length=100)),
                ("zipcode", models.CharField(max_length=100)),
                ("place", models.CharField(blank=True, max_length=100, null=True)),
                ("phone", models.CharField(max_length=100)),
                (
                    "mobile_phone",
                    models.CharField(
                        blank=True, default=None, max_length=100, null=True
                    ),
                ),
                ("paid_amount", models.DecimalField(decimal_places=2, max_digits=8)),
                ("customer_notes", models.TextField(blank=True, null=True)),
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
                    ),
                ),
                (
                    "shipping_price",
                    models.DecimalField(decimal_places=2, default=0, max_digits=8),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                (
                    "sort_order",
                    models.IntegerField(db_index=True, editable=False, null=True),
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
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("price", models.DecimalField(decimal_places=2, max_digits=8)),
                ("quantity", models.IntegerField(default=1)),
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
                "abstract": False,
            },
        ),
    ]
