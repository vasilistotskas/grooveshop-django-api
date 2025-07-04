# Generated by Django 5.2.3 on 2025-06-23 17:22

import django.contrib.postgres.indexes
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0027_alter_blogtag_managers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='blogcomment',
            name='blog_blogco_is_appr_b3338c_btree',
        ),
        migrations.RenameField(
            model_name='blogcomment',
            old_name='is_approved',
            new_name='approved',
        ),
        migrations.AddIndex(
            model_name='blogcomment',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['approved'], name='blog_blogco_approve_964d92_btree'),
        ),
    ]
