# Generated by Django 4.2.8 on 2023-12-05 21:24
from decimal import Decimal

import djmoney.models.fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0003_product_metadata_product_private_metadata_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="discount_value",
            field=djmoney.models.fields.MoneyField(
                decimal_places=2,
                default=Decimal("0"),
                editable=False,
                max_digits=11,
                verbose_name="Discount Value",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="final_price",
            field=djmoney.models.fields.MoneyField(
                decimal_places=2,
                default=Decimal("0"),
                editable=False,
                max_digits=11,
                verbose_name="Final Price",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="price",
            field=djmoney.models.fields.MoneyField(
                decimal_places=2,
                default=Decimal("0"),
                max_digits=11,
                verbose_name="Price",
            ),
        ),
    ]