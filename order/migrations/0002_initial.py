# Generated by Django 4.2.5 on 2023-09-18 00:21
import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("region", "0001_initial"),
        ("pay_way", "0001_initial"),
        ("product", "0001_initial"),
        ("country", "0001_initial"),
        ("order", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="order_item_product",
                to="product.product",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="country",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_country",
                to="country.country",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="pay_way",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_pay_way",
                to="pay_way.payway",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="region",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="order_region",
                to="region.region",
            ),
        ),
    ]
