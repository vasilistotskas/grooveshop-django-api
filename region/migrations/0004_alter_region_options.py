# Generated by Django 5.0.4 on 2024-04-11 17:27

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("region", "0003_alter_region_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="region",
            options={
                "ordering": ["sort_order"],
                "verbose_name": "Region",
                "verbose_name_plural": "Regions",
            },
        ),
    ]
