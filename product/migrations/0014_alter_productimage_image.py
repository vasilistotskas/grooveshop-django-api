# Generated by Django 5.0.6 on 2024-07-02 13:39
from django.db import migrations

import core.fields.image


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0013_alter_producttranslation_description_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productimage",
            name="image",
            field=core.fields.image.ImageAndSvgField(upload_to="uploads/products/", verbose_name="Image"),
        ),
    ]
