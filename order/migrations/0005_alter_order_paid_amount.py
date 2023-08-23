# Generated by Django 4.2.4 on 2023-08-23 06:28

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0004_alter_order_paid_amount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="paid_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=0,
                max_digits=8,
                null=True,
                verbose_name="Paid Amount",
            ),
        ),
    ]
