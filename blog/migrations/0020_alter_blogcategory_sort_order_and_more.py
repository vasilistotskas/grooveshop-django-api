# Generated by Django 5.1 on 2024-08-24 22:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0019_alter_blogcomment_is_approved"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blogcategory",
            name="sort_order",
            field=models.IntegerField(editable=False, null=True, verbose_name="Sort Order"),
        ),
        migrations.AlterField(
            model_name="blogtag",
            name="sort_order",
            field=models.IntegerField(editable=False, null=True, verbose_name="Sort Order"),
        ),
    ]
