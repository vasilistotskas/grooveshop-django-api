# Generated by Django 5.0.3 on 2024-03-31 12:31
import django.contrib.postgres.indexes
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("cart", "0004_alter_cart_user_alter_cartitem_cart_and_more"),
        ("product", "0008_product_product_created_at_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name="cart",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="cart_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="cart",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="cart_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="cart",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["user"], name="cart_cart_user_id_0a6acd_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="cartitem",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="cartitem_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="cartitem",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="cartitem_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="cartitem",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["cart", "product"],
                name="cart_cartit_cart_id_cabd5a_btree",
            ),
        ),
    ]
