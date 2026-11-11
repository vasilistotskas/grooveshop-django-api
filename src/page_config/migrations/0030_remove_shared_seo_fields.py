"""Drop the shared-row SEO columns (step 3 of 3).

The values live on the translations since ``0029_copy_seo_into_translations``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('page_config', '0029_copy_seo_into_translations'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contentpage',
            name='shared_seo_description',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='shared_seo_keywords',
        ),
        migrations.RemoveField(
            model_name='contentpage',
            name='shared_seo_title',
        ),
        migrations.RemoveField(
            model_name='pagelayout',
            name='shared_seo_description',
        ),
        migrations.RemoveField(
            model_name='pagelayout',
            name='shared_seo_keywords',
        ),
        migrations.RemoveField(
            model_name='pagelayout',
            name='shared_seo_title',
        ),
    ]
