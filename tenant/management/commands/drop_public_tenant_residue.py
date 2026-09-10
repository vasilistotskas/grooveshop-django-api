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

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    "\n[dry-run] Nothing dropped. Re-run with --yes to execute."
                )
            )
            return

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
    def _row_counts(tables: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f'select count(*) from public."{table}"')
                counts[table] = cursor.fetchone()[0]
        return counts
