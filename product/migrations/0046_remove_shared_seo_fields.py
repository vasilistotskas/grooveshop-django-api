"""Drop the shared-row SEO columns (step 3 of 3).

The values live on the translations since ``0045_copy_seo_into_translations``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0045_copy_seo_into_translations'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='historicalproduct',
            name='seo_description',
        ),
        migrations.RemoveField(
            model_name='historicalproduct',
            name='seo_keywords',
        ),
        migrations.RemoveField(
            model_name='historicalproduct',
            name='seo_title',
        ),
        migrations.RemoveField(
            model_name='product',
            name='shared_seo_description',
        ),
        migrations.RemoveField(
            model_name='product',
            name='shared_seo_keywords',
        ),
        migrations.RemoveField(
            model_name='product',
            name='shared_seo_title',
        ),
        migrations.RemoveField(
            model_name='productcategory',
            name='shared_seo_description',
        ),
        migrations.RemoveField(
            model_name='productcategory',
            name='shared_seo_keywords',
        ),
        migrations.RemoveField(
            model_name='productcategory',
            name='shared_seo_title',
        ),
    ]
