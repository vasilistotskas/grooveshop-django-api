from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run migrate_schemas in parallel for faster execution at scale."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workers",
            type=int,
            default=10,
            help="Number of parallel workers (default: 10)",
        )

    def handle(self, *args, **options):
        from django.core import management

        from tenant.models import Tenant

        workers = options["workers"]

        # Migrate public schema first (always sequential)
        self.stdout.write("Migrating public schema...")
        management.call_command(
            "migrate_schemas",
            schema_name="public",
            verbosity=0,
        )
        self.stdout.write(self.style.SUCCESS("  public: OK"))

        # Get all tenant schemas
        tenants = list(
            Tenant.objects.exclude(schema_name="public").values_list(
                "schema_name", flat=True
            )
        )

        if not tenants:
            self.stdout.write("No tenant schemas to migrate.")
            return

        self.stdout.write(
            f"Migrating {len(tenants)} tenant schemas with {workers} workers..."
        )

        def migrate_one(schema_name):
            try:
                with schema_context(schema_name):
                    management.call_command(
                        "migrate",
                        verbosity=0,
                    )
                return schema_name, True, ""
            except Exception as exc:
                return schema_name, False, str(exc)

        successes = 0
        failures = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(migrate_one, s): s for s in tenants}
            for future in as_completed(futures):
                name, ok, err = future.result()
                if ok:
                    successes += 1
                    self.stdout.write(f"  {name}: OK")
                else:
                    failures += 1
                    self.stderr.write(
                        self.style.ERROR(f"  {name}: FAILED - {err}")
                    )

        self.stdout.write(f"\nDone: {successes} succeeded, {failures} failed.")
