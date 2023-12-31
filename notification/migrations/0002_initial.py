# Generated by Django 4.2.6 on 2023-10-24 22:01
import django.db.models.deletion
import parler.fields
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notification", "0001_initial"),
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
        migrations.AddConstraint(
            model_name="notificationuser",
            constraint=models.UniqueConstraint(
                fields=("user", "notification"), name="unique_notification_user"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="notificationtranslation",
            unique_together={("language_code", "master")},
        ),
    ]
