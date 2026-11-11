"""Drop the shared-row SEO columns (step 3 of 3).

The values live on the translations since ``0036_copy_seo_into_translations``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0036_copy_seo_into_translations'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='blogpost',
            name='shared_seo_description',
        ),
        migrations.RemoveField(
            model_name='blogpost',
            name='shared_seo_keywords',
        ),
        migrations.RemoveField(
            model_name='blogpost',
            name='shared_seo_title',
        ),
    ]
