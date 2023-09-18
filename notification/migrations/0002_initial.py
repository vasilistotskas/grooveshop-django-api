# Generated by Django 4.2.5 on 2023-09-18 00:21
import django.db.models.deletion
import parler.fields
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("notification", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationuser",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notification",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="notificationtranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="notification.notification",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="notificationuser",
            unique_together={("user", "notification")},
        ),
        migrations.AlterUniqueTogether(
            name="notificationtranslation",
            unique_together={("language_code", "master")},
        ),
    ]
