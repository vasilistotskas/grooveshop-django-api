# Generated by Django 5.0.3 on 2024-03-31 12:31
import django.contrib.postgres.indexes
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "blog",
            "0004_alter_blogpost_author_alter_blogpost_category_and_more",
        ),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name="blogauthor",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="blogauthor_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogauthor",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="blogauthor_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="blogcategory_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="blogcategory_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["sort_order"], name="blogcategory_sort_order_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogcomment",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="blogcomment_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogcomment",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="blogcomment_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="blogpost_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="blogpost_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["published_at"], name="blogpost_published_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["is_published"], name="blogpost_is_published_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["view_count"], name="blog_blogpo_view_co_226064_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["status"], name="blog_blogpo_status_e66397_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="blogpost",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["featured"], name="blog_blogpo_feature_b84efc_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="blogtag",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="blogtag_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogtag",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="blogtag_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogtag",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["sort_order"], name="blogtag_sort_order_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="blogtag",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["active"], name="blog_blogta_active_b685a4_btree"
            ),
        ),
    ]
