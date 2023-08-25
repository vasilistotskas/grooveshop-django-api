# Generated by Django 4.2.4 on 2023-08-25 10:34
import django.db.models.deletion
import mptt.fields
import parler.fields
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("vat", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("product", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="productreview",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_review_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="productimagetranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="product.productimage",
            ),
        ),
        migrations.AddField(
            model_name="productimage",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_images",
                to="product.product",
            ),
        ),
        migrations.AddField(
            model_name="productfavourite",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_favourite",
                to="product.product",
            ),
        ),
        migrations.AddField(
            model_name="productfavourite",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="product_favourite",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="productcategorytranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="product.productcategory",
            ),
        ),
        migrations.AddField(
            model_name="productcategory",
            name="parent",
            field=mptt.fields.TreeForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="product.productcategory",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="category",
            field=mptt.fields.TreeForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="product_category",
                to="product.productcategory",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="vat",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="product_vat",
                to="vat.vat",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="producttranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="productreviewtranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="productimagetranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="productfavourite",
            unique_together={("user", "product")},
        ),
        migrations.AlterUniqueTogether(
            name="productcategorytranslation",
            unique_together={("language_code", "master")},
        ),
    ]
