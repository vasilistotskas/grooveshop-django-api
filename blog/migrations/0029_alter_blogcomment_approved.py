# Generated by Django 5.2.3 on 2025-06-25 12:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0028_remove_blogcomment_blog_blogco_is_appr_b3338c_btree_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='blogcomment',
            name='approved',
            field=models.BooleanField(default=False, verbose_name='Approved'),
        ),
    ]
