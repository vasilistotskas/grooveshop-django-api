# Generated by Django 5.2.1 on 2025-05-14 20:33

import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('tag', '0003_alter_tag_sort_order'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='taggeditem',
            name='tag_taggedi_content_cd6f88_idx',
        ),
        migrations.RenameIndex(
            model_name='tag',
            new_name='tag_created_at_ix',
            old_name='tag_created_at_idx',
        ),
        migrations.RenameIndex(
            model_name='tag',
            new_name='tag_updated_at_ix',
            old_name='tag_updated_at_idx',
        ),
        migrations.RenameIndex(
            model_name='tag',
            new_name='tag_sort_order_ix',
            old_name='tag_sort_order_idx',
        ),
        migrations.RenameIndex(
            model_name='tag',
            new_name='tag_active_ix',
            old_name='tag_tag_active_5d272a_btree',
        ),
        migrations.AddIndex(
            model_name='tag',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['id'], name='tag_id_ix'),
        ),
        migrations.AddIndex(
            model_name='taggeditem',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['created_at'], name='taggeditem_created_at_ix'),
        ),
        migrations.AddIndex(
            model_name='taggeditem',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['updated_at'], name='taggeditem_updated_at_ix'),
        ),
        migrations.AddIndex(
            model_name='taggeditem',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['content_type', 'object_id'], name='tagged_item_content_obj_ix'),
        ),
        migrations.AddIndex(
            model_name='taggeditem',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['tag'], name='tagged_item_tag_ix'),
        ),
        migrations.AddIndex(
            model_name='taggeditem',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['object_id'], name='tagged_item_object_id_ix'),
        ),
    ]
