"""Drop the pre-cutover tenant tables still sitting in the public schema.

Before multi-tenancy this database was a single schema, so every
tenant-app table lived in ``public``. The cutover moved the data into
per-tenant schemas and ``prune_public_legacy_data`` truncated what was
left behind (145 tables, 83,903 rows, 2026-08-22) — but it truncated
ROWS. The tables themselves stayed, and they can be neither maintained
nor used:

* ``TenantSyncRouter.allow_migrate`` returns ``False`` for every app not
  in ``SHARED_APPS`` when the schema is ``public``. Django then skips
  the operations but still writes the ``django_migrations`` row, so the
  public ledger looks identical to a healthy tenant's while no DDL ever
  runs. Measured on production 2026-09-10: ``public.pay_way_payway`` is
  missing ``settlement``, ``public.order_order`` is missing 12 columns,
  and 18 tables added since the cutover were never created at all —
  while the ledger claims every migration applied.
* Nothing reads them. Tenant models are served only inside a tenant
  schema; ``public`` is the control plane.

Leaving them is not harmless: a tenant query that runs in a public
context gets an EMPTY RESULT from a stale table instead of an error —
silent wrongness rather than a loud failure — and every audit of the
database has to know to ignore 145 tables.

Why a command and not a migration
---------------------------------
A migration was written first and was wrong. The ``tests`` lane strips
``DATABASE_ROUTERS`` to keep the suite fast, so it is routerless and
every table legitimately lives in ``public`` there. With no router,
``allow_migrate`` returns ``True``, the migration ran during test-database
setup and dropped the entire schema — 378 failures locally, and it would
have done the same in CI on every run. A cleanup that fires implicitly
on every database, forever, to fix one database lineage once, is the
wrong shape. This is the shape ``prune_public_legacy_data`` used, for
the same reason.

Guards
------
* Dry run by default; ``--yes`` is required to execute.
* Refuses unless the connection is on the public schema.
* Refuses unless the django-tenants router is installed — without it
  the "tenant tables do not belong in public" invariant is simply not
  true, which is exactly the trap the migration fell into.
* One ``DROP TABLE`` for the whole set, **without CASCADE**. Postgres
  tolerates foreign keys inside the set and refuses on anything
  pointing in from outside, so an unexpected dependency aborts instead
  of quietly cascading into shared data.
* Names only tables that actually exist, so it is a no-op on any
  database created after the cutover.

Legacy inbound foreign keys
---------------------------
The no-CASCADE guard fires on a real database lineage, not a
hypothetical one. ``UserAccount.loyalty_tier`` declares
``db_constraint=False`` — UserAccount is SHARED, LoyaltyTier is TENANT,
and PostgreSQL cannot enforce a cross-schema FK — but a pre-cutover
database still carries the CONSTRAINT Django created back when both
tables shared one schema. The drop then aborts with "constraint
user_useraccount_loyalty_tier_id_... depends on table loyalty_tier",
and the residue is unreachable.

``prune_public_legacy_data`` handled this and was deleted with the rest
of that command, so the repair disappeared from the repo while the
databases that need it did not. It is back here: inbound FKs whose
REFERENCING table sits outside the residue set are neutralised first —
the column is NULLed and the constraint dropped — and only when every
such column is nullable. A NOT NULL column means the reference is real
and load-bearing, so the command aborts and says which one.

Confined to constraints pointing INTO the residue from outside it.
FKs wholly inside the set are dropped with their tables, which is why
``DROP TABLE`` lists them all in one statement.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import get_public_schema_name

from tenant.app_labels import tenant_only_table_names

ROUTER_PATH = "django_tenants.routers.TenantSyncRouter"


class Command(BaseCommand):
    help = (
        "Drop tenant-only tables left in the public schema by the "
        "pre-multi-tenant era. Dry run unless --yes is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually drop the tables. Without this, only report.",
        )

    def handle(self, *args, **options):
        self._check_preconditions()

        public = get_public_schema_name()
        residue = self._residue(public)

        if not residue:
            self.stdout.write(
                self.style.SUCCESS(
                    f"No tenant-only tables in the {public} schema — "
                    f"nothing to do."
                )
            )
            return

        counts = self._row_counts(residue)
        total_rows = sum(counts.values())

        self.stdout.write(
            f"{len(residue)} tenant-only table(s) in the {public} "
            f"schema, holding {total_rows} row(s) in total:"
        )
        for table in residue:
            rows = counts[table]
            line = f"  {table:52} rows={rows}"
            self.stdout.write(self.style.WARNING(line) if rows else line)

        inbound = self._legacy_inbound_fks(public, residue)
        if inbound:
            self.stdout.write(
                f"\n{len(inbound)} legacy inbound foreign key(s) point "
                f"into the residue from outside it:"
            )
            for fk in inbound:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {fk['table']}.{fk['column']} -> "
                        f"{fk['references']}  ({fk['name']})"
                    )
                )

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    "\n[dry-run] Nothing dropped. Re-run with --yes to execute."
                )
            )
            return

        self._neutralize_legacy_inbound_fks(inbound)

        quoted = ", ".join(f'{public}."{table}"' for table in residue)
        with connection.cursor() as cursor:
            # No CASCADE, deliberately — see the module docstring.
            cursor.execute(f"DROP TABLE {quoted}")

        remaining = self._residue(public)
        if remaining:
            raise CommandError(
                f"{len(remaining)} table(s) still present after the "
                f"drop: {remaining[:5]}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Dropped {len(residue)} table(s) from the {public} schema."
            )
        )

    def _check_preconditions(self) -> None:
        if ROUTER_PATH not in set(settings.DATABASE_ROUTERS):
            raise CommandError(
                "The django-tenants router is not installed, so tenant "
                "tables in the public schema are not residue — in a "
                "routerless configuration that is where they belong. "
                "Refusing to drop anything."
            )

        public = get_public_schema_name()
        current = getattr(connection, "schema_name", None)
        if current != public:
            raise CommandError(
                f"Connected to schema {current!r}, not {public!r}. This "
                f"command only ever operates on the public schema."
            )

    @staticmethod
    def _residue(public: str) -> list[str]:
        expected = tenant_only_table_names()
        with connection.cursor() as cursor:
            cursor.execute(
                "select table_name from information_schema.tables "
                "where table_schema = %s and table_type = 'BASE TABLE'",
                [public],
            )
            present = {row[0] for row in cursor.fetchall()}
        return sorted(present & expected)

    @staticmethod
    def _legacy_inbound_fks(public: str, residue: list[str]) -> list[dict]:
        """FK constraints pointing INTO the residue from outside it.

        These are the ones ``DROP TABLE`` without CASCADE refuses on.
        Constraints wholly inside the residue are ignored: they go with
        their tables in the same statement.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select con.conname,
                       src.relname,
                       att.attname,
                       tgt.relname,
                       att.attnotnull
                  from pg_constraint con
                  join pg_class src on src.oid = con.conrelid
                  join pg_class tgt on tgt.oid = con.confrelid
                  join pg_namespace ns on ns.oid = src.relnamespace
                  join unnest(con.conkey) as k(attnum) on true
                  join pg_attribute att
                    on att.attrelid = con.conrelid
                   and att.attnum = k.attnum
                 where con.contype = 'f'
                   and ns.nspname = %s
                   and tgt.relname = any(%s)
                   and src.relname <> all(%s)
                 order by src.relname, con.conname
                """,
                [public, list(residue), list(residue)],
            )
            return [
                {
                    "name": name,
                    "table": table,
                    "column": column,
                    "references": referenced,
                    "not_null": not_null,
                }
                for name, table, column, referenced, not_null in (
                    cursor.fetchall()
                )
            ]

    def _neutralize_legacy_inbound_fks(self, inbound: list[dict]) -> None:
        """NULL the referencing column and drop the debris constraint.

        Aborts on a NOT NULL column rather than inventing a value: that
        is a real reference the cutover did not sever, and dropping the
        table it points at would be data loss, not cleanup.
        """
        if not inbound:
            return

        blocking = [fk for fk in inbound if fk["not_null"]]
        if blocking:
            detail = ", ".join(
                f"{fk['table']}.{fk['column']}" for fk in blocking
            )
            raise CommandError(
                f"Refusing to neutralize NOT NULL reference(s) into the "
                f"residue: {detail}. A non-nullable column pointing at a "
                f"tenant table means the cutover left a real dependency "
                f"behind; resolve it deliberately before dropping."
            )

        with connection.cursor() as cursor:
            for fk in inbound:
                cursor.execute(
                    f'update public."{fk["table"]}" '
                    f'set "{fk["column"]}" = null '
                    f'where "{fk["column"]}" is not null'
                )
                cleared = cursor.rowcount
                cursor.execute(
                    f'alter table public."{fk["table"]}" '
                    f'drop constraint "{fk["name"]}"'
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  neutralized {fk['table']}.{fk['column']} "
                        f"({cleared} row(s) cleared), dropped {fk['name']}"
                    )
                )

    @staticmethod
    def _row_counts(tables: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f'select count(*) from public."{table}"')
                counts[table] = cursor.fetchone()[0]
        return counts
