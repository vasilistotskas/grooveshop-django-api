# Generated by Django 4.2.4 on 2023-09-03 15:04
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0003_alter_useraddress_floor_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="useraccount",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "User Account",
                "verbose_name_plural": "User Accounts",
            },
        ),
    ]