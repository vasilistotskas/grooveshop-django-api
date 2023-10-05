# Generated by Django 4.2.5 on 2023-10-05 07:56
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("country", "0001_initial"),
        ("region", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserAccount",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
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
                    "email",
                    models.EmailField(
                        max_length=254, unique=True, verbose_name="Email Address"
                    ),
                ),
                (
                    "first_name",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="First Name"
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Last Name"
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Phone"
                    ),
                ),
                (
                    "city",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="City"
                    ),
                ),
                (
                    "zipcode",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Zip Code"
                    ),
                ),
                (
                    "address",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Address"
                    ),
                ),
                (
                    "place",
                    models.CharField(
                        blank=True, max_length=255, null=True, verbose_name="Place"
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="uploads/users/",
                        verbose_name="Image",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("is_staff", models.BooleanField(default=False, verbose_name="Staff")),
                (
                    "birth_date",
                    models.DateField(blank=True, null=True, verbose_name="Birth Date"),
                ),
                (
                    "country",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="user_account_country",
                        to="country.country",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "region",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="user_account_region",
                        to="region.region",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "verbose_name": "User Account",
                "verbose_name_plural": "User Accounts",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="UserAddress",
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
                ("title", models.CharField(max_length=255, verbose_name="Title")),
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
                ("zipcode", models.CharField(max_length=255, verbose_name="Zip Code")),
                (
                    "floor",
                    models.PositiveSmallIntegerField(
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
                        null=True,
                        verbose_name="Floor",
                    ),
                ),
                (
                    "location_type",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        choices=[(0, "Home"), (1, "Office"), (2, "Other")],
                        default=None,
                        null=True,
                        verbose_name="Location Type",
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        default=None,
                        max_length=255,
                        null=True,
                        verbose_name="Phone Number",
                    ),
                ),
                (
                    "mobile_phone",
                    models.CharField(
                        blank=True,
                        default=None,
                        max_length=255,
                        null=True,
                        verbose_name="Mobile Phone Number",
                    ),
                ),
                (
                    "notes",
                    models.CharField(
                        blank=True,
                        default=None,
                        max_length=255,
                        null=True,
                        verbose_name="Notes",
                    ),
                ),
                ("is_main", models.BooleanField(default=False, verbose_name="Is Main")),
                (
                    "country",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="address_country",
                        to="country.country",
                    ),
                ),
                (
                    "region",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="address_region",
                        to="region.region",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_address",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "User Address",
                "verbose_name_plural": "User Addresses",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="useraddress",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_main", True)),
                fields=("user", "is_main"),
                name="unique_main_address",
            ),
        ),
    ]
