# Generated by Django 5.0.6 on 2024-07-07 14:34

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0004_alter_country_options"),
        ("region", "0004_alter_region_options"),
        ("user", "0010_alter_useraccount_image"),
    ]

    operations = [
        migrations.AlterField(
            model_name="useraccount",
            name="country",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="residents",
                to="country.country",
            ),
        ),
        migrations.AlterField(
            model_name="useraccount",
            name="region",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="residents",
                to="region.region",
            ),
        ),
        migrations.AlterField(
            model_name="useraddress",
            name="country",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="user_addresses",
                to="country.country",
            ),
        ),
        migrations.AlterField(
            model_name="useraddress",
            name="region",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="user_addresses",
                to="region.region",
            ),
        ),
        migrations.AlterField(
            model_name="useraddress",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="addresses", to=settings.AUTH_USER_MODEL
            ),
        ),
    ]
