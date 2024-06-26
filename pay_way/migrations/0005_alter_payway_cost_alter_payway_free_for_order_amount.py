# Generated by Django 5.0.4 on 2024-04-17 15:30
from decimal import Decimal

import djmoney.models.fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0004_alter_payway_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payway",
            name="cost",
            field=djmoney.models.fields.MoneyField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=11,
                verbose_name="Cost",
            ),
        ),
        migrations.AlterField(
            model_name="payway",
            name="free_for_order_amount",
            field=djmoney.models.fields.MoneyField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=11,
                verbose_name="Free For Order Amount",
            ),
        ),
    ]
