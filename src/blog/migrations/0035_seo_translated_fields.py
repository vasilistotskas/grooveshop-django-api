"""Add the SEO fields to the blog translations models (step 1 of 3).

The shared-row columns are renamed to ``shared_seo_*`` first: parler
refuses a translated field whose name the shared model already has
(``TranslatedFieldsModelMixin.contribute_translations`` raises
``TypeError``), so the two cannot coexist under one name even for the
one migration that copies between them.

See ``0036_copy_seo_into_translations`` for why and how the values move.
"""

import core.fields.plain_text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0034_seo_description_plain_text'),
    ]

    operations = [
        migrations.RenameField(
            model_name='blogpost',
            old_name='seo_title',
            new_name='shared_seo_title',
        ),
        migrations.RenameField(
            model_name='blogpost',
            old_name='seo_description',
            new_name='shared_seo_description',
        ),
        migrations.RenameField(
            model_name='blogpost',
            old_name='seo_keywords',
            new_name='shared_seo_keywords',
        ),
        migrations.AddField(
            model_name='blogposttranslation',
            name='seo_description',
            field=core.fields.plain_text.PlainTextField(blank=True, default='', max_length=300, verbose_name='Seo Description'),
        ),
        migrations.AddField(
            model_name='blogposttranslation',
            name='seo_keywords',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Seo Keywords'),
        ),
        migrations.AddField(
            model_name='blogposttranslation',
            name='seo_title',
            field=models.CharField(blank=True, default='', max_length=70, verbose_name='Seo Title'),
        ),
    ]
