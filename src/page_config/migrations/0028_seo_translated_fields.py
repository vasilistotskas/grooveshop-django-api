"""Add the SEO fields to the page_config translations models (step 1 of 3).

The shared-row columns are renamed to ``shared_seo_*`` first: parler
refuses a translated field whose name the shared model already has
(``TranslatedFieldsModelMixin.contribute_translations`` raises
``TypeError``), so the two cannot coexist under one name even for the
one migration that copies between them.

See ``0029_copy_seo_into_translations`` for why and how the values move.
"""

import core.fields.plain_text
import django.db.models.deletion
import parler.fields
import parler.models
from django.db import migrations, models


class AlterModelBases(migrations.operations.base.Operation):
    """Change a model's bases in the migration STATE only.

    ``PageLayout`` becomes a ``TranslatableModel`` here, and the
    autodetector does not track bases (Django ticket #23521), so the
    historical model would otherwise stay a plain ``models.Model``.
    Parler then refuses the new ``PageLayoutTranslation`` in every later
    migration's state: its ``TranslationsForeignKey`` calls
    ``contribute_translations``, which requires ``_parler_meta`` on the
    shared model ("does not appear to inherit from TranslatableModel").
    ``ContentPage``'s own ``CreateModel`` carries the same bases.

    Bases have no database representation, so both database methods
    are no-ops; unapplying needs nothing either, because Django rebuilds
    the earlier state by replaying the operations before this one.
    """

    reduces_to_sql = False
    reversible = True

    def __init__(self, name, bases):
        self.name = name
        self.bases = bases

    def deconstruct(self):
        return (
            self.__class__.__qualname__,
            [],
            {"name": self.name, "bases": self.bases},
        )

    def state_forwards(self, app_label, state):
        model_key = (app_label, self.name.lower())
        state.models[model_key].bases = self.bases
        state.reload_model(*model_key, delay=True)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def database_backwards(
        self, app_label, schema_editor, from_state, to_state
    ):
        pass

    def describe(self):
        return f"Alter bases of {self.name}"


class Migration(migrations.Migration):

    dependencies = [
        ('page_config', '0027_alter_pagesection_component_type'),
    ]

    operations = [
        AlterModelBases(
            name="PageLayout",
            bases=(parler.models.TranslatableModelMixin, models.Model),
        ),
        migrations.RenameField(
            model_name='contentpage',
            old_name='seo_title',
            new_name='shared_seo_title',
        ),
        migrations.RenameField(
            model_name='contentpage',
            old_name='seo_description',
            new_name='shared_seo_description',
        ),
        migrations.RenameField(
            model_name='contentpage',
            old_name='seo_keywords',
            new_name='shared_seo_keywords',
        ),
        migrations.RenameField(
            model_name='pagelayout',
            old_name='seo_title',
            new_name='shared_seo_title',
        ),
        migrations.RenameField(
            model_name='pagelayout',
            old_name='seo_description',
            new_name='shared_seo_description',
        ),
        migrations.RenameField(
            model_name='pagelayout',
            old_name='seo_keywords',
            new_name='shared_seo_keywords',
        ),
        migrations.AddField(
            model_name='contentpagetranslation',
            name='seo_description',
            field=core.fields.plain_text.PlainTextField(blank=True, default='', max_length=300, verbose_name='Seo Description'),
        ),
        migrations.AddField(
            model_name='contentpagetranslation',
            name='seo_keywords',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Seo Keywords'),
        ),
        migrations.AddField(
            model_name='contentpagetranslation',
            name='seo_title',
            field=models.CharField(blank=True, default='', max_length=70, verbose_name='Seo Title'),
        ),
        migrations.CreateModel(
            name='PageLayoutTranslation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seo_title', models.CharField(blank=True, default='', max_length=70, verbose_name='Seo Title')),
                ('seo_description', core.fields.plain_text.PlainTextField(blank=True, default='', max_length=300, verbose_name='Seo Description')),
                ('seo_keywords', models.CharField(blank=True, default='', max_length=255, verbose_name='Seo Keywords')),
                ('language_code', models.CharField(db_index=True, max_length=15, verbose_name='Language')),
                ('master', parler.fields.TranslationsForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='translations', to='page_config.pagelayout')),
            ],
            options={
                'verbose_name': 'Page Layout Translation',
                'verbose_name_plural': 'Page Layout Translations',
                'db_table': 'page_config_pagelayout_translation',
                'unique_together': {('language_code', 'master')},
            },
            bases=(parler.models.TranslatedFieldsModelMixin, models.Model),
        ),
    ]
