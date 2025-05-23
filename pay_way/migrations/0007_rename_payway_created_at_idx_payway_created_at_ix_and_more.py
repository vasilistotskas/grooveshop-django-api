# Generated by Django 5.2.1 on 2025-05-14 20:33

import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pay_way', '0006_alter_payway_sort_order'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='payway',
            new_name='payway_created_at_ix',
            old_name='payway_created_at_idx',
        ),
        migrations.RenameIndex(
            model_name='payway',
            new_name='payway_updated_at_ix',
            old_name='payway_updated_at_idx',
        ),
        migrations.RenameIndex(
            model_name='payway',
            new_name='payway_sort_order_ix',
            old_name='payway_sort_order_idx',
        ),
        migrations.RenameIndex(
            model_name='payway',
            new_name='pay_way_active_ix',
            old_name='pay_way_pay_active_1c54dd_btree',
        ),
        migrations.AddIndex(
            model_name='payway',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['cost'], name='pay_way_cost_ix'),
        ),
        migrations.AddIndex(
            model_name='payway',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['free_for_order_amount'], name='pay_way_free_order_ix'),
        ),
    ]
