# Generated by Django 5.0.3 on 2024-03-08 15:04
import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_initial"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="site",
            field=models.ForeignKey(
                default=1,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="settings",
                to="sites.site",
                verbose_name="Site",
            ),
            preserve_default=False,
        ),
        migrations.AlterUniqueTogether(
            name="settings",
            unique_together={("site", "key")},
        ),
    ]
