# Generated by Django 4.2.4 on 2023-08-31 13:54
import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("cart", "0001_initial"),
        ("product", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="cartitem",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="cart_item_product",
                to="product.product",
            ),
        ),
    ]
