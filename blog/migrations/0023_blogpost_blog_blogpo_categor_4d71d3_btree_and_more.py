# Generated by Django 5.1.2 on 2024-10-26 08:10
import django.contrib.postgres.indexes
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0022_alter_blogposttranslation_master"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["category"], name="blog_blogpo_categor_4d71d3_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["author"], name="blog_blogpo_author__753b9d_btree"
            ),
        ),
    ]