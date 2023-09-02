# Generated by Django 4.2.4 on 2023-09-01 15:01
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0002_alter_useraddress_floor_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="useraddress",
            name="floor",
            field=models.PositiveSmallIntegerField(
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
        migrations.AlterField(
            model_name="useraddress",
            name="location_type",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[(0, "Home"), (1, "Office"), (2, "Other")],
                default=None,
                null=True,
                verbose_name="Location Type",
            ),
        ),
    ]
