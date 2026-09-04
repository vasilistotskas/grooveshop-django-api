"""Metadata-only: the username help text became translatable.

It was built with an f-string inside `gettext_lazy`, so the msgid carried
the interpolated number and never matched the catalogue — Greek users
read it in English. It also lost a space, concatenating to
"fewer.Letters". Both are fixed with `format_lazy`.

Emits NO DDL. Verified against this Postgres backend:
`schema_editor._field_should_be_altered()` returns False when only
`help_text` differs, and True for a real change such as `max_length`. So
this is safe to run ahead of the rollout under the PreSync hook.
"""

import user.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0026_admin_log_public_actor_fk"),
    ]

    operations = [
        migrations.AlterField(
            model_name="useraccount",
            name="username",
            field=models.CharField(
                blank=True,
                error_messages={
                    "unique": "A user with that username already exists."
                },
                help_text="Required. 30 characters or fewer. Letters, digits and @/./+/-/_ only.",
                max_length=30,
                null=True,
                unique=True,
                validators=[user.validators.ExtendedUnicodeUsernameValidator()],
                verbose_name="Username",
            ),
        ),
    ]
