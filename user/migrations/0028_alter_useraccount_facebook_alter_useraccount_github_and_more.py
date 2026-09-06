"""Stop every new account from being born with seven NULL profile URLs.

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

No code ever assigned these — that is precisely the problem. With
``null=True`` and no default, ``Field.get_default()`` returns None, so
each of the seven was NULL on every account ever created, while the
admin's own 'clear socials' action wrote "" to the same columns. The
read serializer then declared each one ``nullable`` with the
description "URL link or empty string" — the ambiguity, written down
in the public API contract.

``phone`` joins them. Ruff never flagged it — ``DJ001`` matches Django's
own field classes and ``PhoneNumberField`` is a third-party subclass of
``CharField`` — but it is the same defect, and here it was chosen
outright: ``default=None``. The account filter has been paying for it
ever since, spelling "has no phone" as ``phone__isnull=True OR
phone=""`` on a column that is not even behind a join.
"""


import phonenumber_field.modelfields
from core.db.migration_operations import BackfillNullStringsToEmpty
from django.db import migrations, models


class Migration(migrations.Migration):
    # The backfill commits per batch; one transaction around all
    # of them would hold every row lock to the end.
    atomic = False

    dependencies = [
        ('user', '0027_username_help_text'),
    ]

    operations = [
        migrations.AlterField(
            model_name='useraccount',
            name='facebook',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Facebook Profile'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='github',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Github Profile'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='instagram',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Instagram Profile'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='linkedin',
            field=models.URLField(blank=True, default='', null=True, verbose_name='LinkedIn Profile'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='phone',
            field=phonenumber_field.modelfields.PhoneNumberField(blank=True, default='', max_length=128, null=True, region=None, verbose_name='Phone Number'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='twitter',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Twitter Profile'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='website',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Website'),
        ),
        migrations.AlterField(
            model_name='useraccount',
            name='youtube',
            field=models.URLField(blank=True, default='', null=True, verbose_name='Youtube Profile'),
        ),
        BackfillNullStringsToEmpty(
            'useraccount',
            [
                'twitter',
                'linkedin',
                'facebook',
                'instagram',
                'website',
                'youtube',
                'github',
                'phone',
            ],
        ),
    ]
