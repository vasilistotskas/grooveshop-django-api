"""Drop the orphaned ``token_blacklist`` tables.

``djangorestframework-simplejwt`` was replaced by Knox, but removing it
from ``INSTALLED_APPS`` left its tables, their FK constraints and its 11
``django_migrations`` rows behind in the public schema.

That debris is not inert. Django's deletion collector only cascades
relations it can see through the app registry, and an uninstalled app
has no models there — while the DATABASE constraint is still very much
enforced. Deleting a user therefore failed at COMMIT with
``violates foreign key constraint "token_blacklist_outs_user_id_..."``
mid-way through the public-schema cleanup on 2026-08-21. The rollback
was clean, but the failure mode is nasty: it surfaces only at commit,
names a table nobody recognises, and blocks an operation that has no
apparent connection to JWT.

Done as a migration rather than one-off SQL so staging, development and
any future environment converge on the same schema instead of carrying
the debris until someone trips over it again.

Scope: ``core`` is in SHARED_APPS only, and these tables exist only in
public (verified in production 2026-08-22) — they predate the
multi-tenant split, so no tenant schema has a copy.

Irreversible by design: ``reverse_sql`` is a no-op. Recreating empty
tables for a package that is no longer installed would restore the
hazard without restoring anything useful; the rows are in the
pre-cleanup dump if they are ever genuinely needed.
"""

from django.db import migrations

DROP_TABLES = """
DROP TABLE IF EXISTS token_blacklist_blacklistedtoken CASCADE;
DROP TABLE IF EXISTS token_blacklist_outstandingtoken CASCADE;
"""

# Without this the app looks half-installed: ``showmigrations`` lists an
# app that has no code, and a future ``migrate`` for a reinstated
# simplejwt would think its schema already exists.
FORGET_MIGRATIONS = """
DELETE FROM django_migrations WHERE app = 'token_blacklist';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_add_cache_purge_log"),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_TABLES,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=FORGET_MIGRATIONS,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
