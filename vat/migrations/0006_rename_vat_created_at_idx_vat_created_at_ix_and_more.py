# Generated by Django 5.2.1 on 2025-05-14 20:33

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('vat', '0005_alter_vat_value_vat_vat_value_range'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='vat',
            new_name='vat_created_at_ix',
            old_name='vat_created_at_idx',
        ),
        migrations.RenameIndex(
            model_name='vat',
            new_name='vat_updated_at_ix',
            old_name='vat_updated_at_idx',
        ),
        migrations.RenameIndex(
            model_name='vat',
            new_name='vat_value_ix',
            old_name='vat_vat_value_d54579_btree',
        ),
    ]
