# Generated by Django 5.0.4 on 2024-04-11 17:27
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0010_alter_productcategory_options_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="productcategory",
            options={
                "ordering": ["sort_order"],
                "verbose_name": "Product Category",
                "verbose_name_plural": "Product Categories",
            },
        ),
        migrations.AlterModelOptions(
            name="productimage",
            options={
                "ordering": ["sort_order"],
                "verbose_name": "Product Image",
                "verbose_name_plural": "Product Images",
            },
        ),
    ]
