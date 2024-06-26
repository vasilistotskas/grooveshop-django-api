# Generated by Django 5.0.4 on 2024-04-26 12:52
import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("country", "0004_alter_country_options"),
        ("region", "0004_alter_region_options"),
        ("user", "0007_useraccount_username"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="useraccount",
            name="user_account_trgm_idx",
        ),
        migrations.AddIndex(
            model_name="useraccount",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=[
                    "email",
                    "username",
                    "first_name",
                    "last_name",
                    "phone",
                    "city",
                    "zipcode",
                    "address",
                    "place",
                ],
                name="user_account_trgm_idx",
                opclasses=[
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                    "gin_trgm_ops",
                ],
            ),
        ),
    ]
