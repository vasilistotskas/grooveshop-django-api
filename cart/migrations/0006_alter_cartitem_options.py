# Generated by Django 5.0.4 on 2024-04-11 11:43

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cart", "0005_cart_cart_created_at_idx_cart_cart_updated_at_idx_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="cartitem",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Cart Item",
                "verbose_name_plural": "Cart Items",
            },
        ),
    ]
