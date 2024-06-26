# Generated by Django 5.0.3 on 2024-03-29 20:53
import django.db.models.deletion
import mptt.fields
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0006_remove_product_product_search_vector_idx_and_more"),
        ("vat", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="category",
            field=mptt.fields.TreeForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="products",
                to="product.productcategory",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="vat",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="products",
                to="vat.vat",
            ),
        ),
        migrations.AlterField(
            model_name="productreview",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reviews",
                to="product.product",
            ),
        ),
        migrations.AlterField(
            model_name="productreview",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_reviews",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
