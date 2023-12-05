# Generated by Django 4.2.8 on 2023-12-05 21:24

from decimal import Decimal
from django.db import migrations
import djmoney.models.fields


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0001_initial"),
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
