# Generated by Django 4.2.4 on 2023-09-03 15:04
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0002_initial"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="product",
            new_name="product_search_vector_idx",
            old_name="product_pro_search__e78047_gin",
        ),
    ]
