# Generated by Django 5.0.4 on 2024-04-11 17:27
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0003_alter_payway_options"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="payway",
            options={
                "ordering": ["sort_order"],
                "verbose_name": "Pay Way",
                "verbose_name_plural": "Pay Ways",
            },
        ),
    ]
