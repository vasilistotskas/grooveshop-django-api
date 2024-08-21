# Generated by Django 5.0.8 on 2024-08-20 00:34

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notification", "0003_notification_notification_created_at_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationuser",
            name="notification",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="user", to="notification.notification"
            ),
        ),
    ]
