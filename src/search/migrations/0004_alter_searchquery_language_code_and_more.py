"""Stop the search-analytics row from collecting NULL strings.

Step ONE of a two-release change. A string column that allows NULL has
two spellings for "no value"; ruff's ``DJ001`` flags all thirteen in this
project. Under the Argo CD PreSync hook the ALTER that removes the NULL
spelling lands BEFORE the new image rolls, so it would run while pods
that still write NULL are serving. This release therefore only stops
PRODUCING NULL and rewrites what is already stored; the ``NOT NULL``
constraint is the next release's job.

The ``AlterField``s below emit no DDL — ``sqlmigrate`` renders each one
``(no-op)``. A Python-level ``default`` never reaches Postgres (only
``db_default`` does), so their whole effect is on ``Field.get_default()``:
without a default, a nullable ``CharField`` defaults to ``None``, which
is how every one of these columns kept collecting NULLs.

All three columns had live NULL writers. ``language_code`` came
straight from ``request.GET.get("language_code")`` — None whenever the
storefront omitted the parameter. ``session_key`` came from
``request.session.session_key``, which Django leaves None until a
session is first PERSISTED, so an anonymous search — the common case
for this table — always wrote NULL. ``user_agent`` was already sent as
"" by the middleware but written as NULL by anything that omitted it,
and the retention scrubber wrote "" to that column while writing NULL
to ``session_key`` right beside it.
"""


import django.core.validators
from core.db.migration_operations import BackfillNullStringsToEmpty
from django.db import migrations, models


class Migration(migrations.Migration):
    # The backfill commits per batch; one transaction around all
    # of them would hold every row lock to the end.
    atomic = False

    dependencies = [
        ('search', '0003_searchquery_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='searchquery',
            name='language_code',
            field=models.CharField(blank=True, db_index=True, default='', help_text="Language code for the search (e.g., 'en', 'el', 'de')", max_length=10, null=True),
        ),
        migrations.AlterField(
            model_name='searchquery',
            name='session_key',
            field=models.CharField(blank=True, default='', help_text='Session key for anonymous users', max_length=40, null=True),
        ),
        migrations.AlterField(
            model_name='searchquery',
            name='user_agent',
            field=models.TextField(blank=True, default='', help_text='User agent string from the request', null=True, validators=[django.core.validators.MaxLengthValidator(512)]),
        ),
        BackfillNullStringsToEmpty(
            'searchquery',
            ['language_code', 'session_key', 'user_agent'],
        ),
    ]
