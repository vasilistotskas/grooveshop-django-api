"""Drop ``django_admin_log.user_id``'s FK in TENANT schemas only.

An admin-log row records a PUBLIC-schema actor acting on a
TENANT-schema object, and PostgreSQL cannot express a cross-schema
foreign key. ``content_type_id`` is the one that must stay real (a
tenant's content-type id space differs from public's — verified in
production), so ``user_id`` is the one that gives up its constraint,
exactly as ``product.changed_by``, ``order.stock_log`` and the
ACS/BoxNow shipment histories already do via ``db_constraint=False``.

Without this, a freshly provisioned tenant fails in two stages:

1. Immediately — the tenant's ``user_useraccount`` holds no row for the
   owner's public pk, so their first admin save raises
   ``ForeignKeyViolation`` and 500s.
2. Later — once the tenant has enough customers to cover that pk range
   the constraint is satisfied by a SHOPPER, so nothing errors and the
   history silently credits the wrong person.

webside survived only because the cutover copied users id-preserving,
which made every platform pk coincidentally resolve to the same person
(verified in production: all five platform identities matched).

Lives in ``user`` rather than ``admin``: the project's ``admin/``
package is an ``AppConfig`` for ``django.contrib.admin`` and owns no
migrations, while ``user`` is dual-listed into SHARED_APPS and
TENANT_APPS, so this runs in every schema and the guard below decides.

Model state is deliberately untouched — the ORM keeps treating
``LogEntry.user`` as a FK (``select_related`` and friends still work);
only the database constraint goes. Reading the actor across the
boundary is handled by ``admin.log_actors``.
"""

from django.db import migrations
from django_tenants.utils import get_public_schema_name

# Django names this constraint deterministically, but the hash suffix
# is an implementation detail — look it up instead of hardcoding it.
_FIND_CONSTRAINT = """
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE rel.relname = 'django_admin_log'
      AND con.contype = 'f'
      AND nsp.nspname = %s
      AND pg_get_constraintdef(con.oid) LIKE 'FOREIGN KEY (user_id)%%'
"""


def drop_public_actor_fk(apps, schema_editor):
    """Drop the actor FK when running against a tenant schema."""
    connection = schema_editor.connection
    schema_name = getattr(connection, "schema_name", None)

    # Public keeps a REAL constraint: there the actor genuinely is a
    # local row, and the platform admin must not lose that integrity.
    if schema_name is None or schema_name == get_public_schema_name():
        return

    with connection.cursor() as cursor:
        cursor.execute(_FIND_CONSTRAINT, [schema_name])
        row = cursor.fetchone()
        if row is None:
            # Already dropped, or the table has not been created in
            # this schema yet. Both are fine — this is idempotent.
            return
        cursor.execute(
            'ALTER TABLE "{}"."django_admin_log" DROP CONSTRAINT "{}"'.format(
                schema_name, row[0]
            )
        )


class Migration(migrations.Migration):
    dependencies = [
        (
            "user",
            "0025_alter_subscriptiontopictranslation_unique_together_and_more",
        ),
        # The table must exist before its constraint can be dropped.
        ("admin", "0003_logentry_add_action_flag_choices"),
    ]

    operations = [
        migrations.RunPython(
            drop_public_actor_fk,
            # Reverse is a no-op on purpose: re-adding the constraint
            # would fail on exactly the tenants this migration exists
            # to support (their log rows reference public pks that do
            # not exist locally). Leaving it dropped is safe — it
            # relaxes integrity, it does not lose data.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
