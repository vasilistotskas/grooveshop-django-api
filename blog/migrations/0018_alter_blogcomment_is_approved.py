# Generated by Django 5.0.8 on 2024-08-19 15:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0017_alter_blogcomment_likes_alter_blogcomment_post_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blogcomment",
            name="is_approved",
            field=models.BooleanField(default=True, verbose_name="Is Approved"),
        ),
    ]
