# Generated by Django 5.0.4 on 2024-04-11 17:27

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tip", "0003_alter_tip_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tip",
            options={
                "ordering": ["sort_order"],
                "verbose_name": "Tip",
                "verbose_name_plural": "Tips",
            },
        ),
    ]
