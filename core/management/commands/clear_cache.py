"""Purge cached keys, per tenant schema.

WHY THIS IS PER-SCHEMA, and why the default changed:

``CACHES["default"]["KEY_FUNCTION"]`` is ``tenant.cache.make_tenant_key``,
which prefixes every Django cache key with the ACTIVE schema
(``{schema}:{prefix}:{version}:{key}``), and ``CustomCache._make_pattern``
builds its SCAN pattern through the same function. That is correct and
deliberate for the admin path — a request carries a tenant, so a tenant
admin only ever sees its own keys.

A management command carries no tenant. It runs in ``public``, so its
SCAN only ever matched public's keys and it reported ``django=0`` for
every surface while a tenant held hundreds. Measured on staging
2026-09-01: ``clear_cache --all`` reported 0 Django keys across all ten
surfaces; the same surfaces under ``schema_context('webside')`` matched
and purged 436. With ``DEFAULT_CACHE_TTL`` at 7200s+ that is how long a
merchant's edit could stay invisible after an operator "purged" it.

So the CLI now purges every active tenant by default. Use ``--schema``
for one tenant, or ``--public-only`` for the old behaviour (the platform
control-plane's own keys).

The Nuxt half is unaffected by any of this — it goes over HTTP to the
storefront's purge endpoint, which scopes by tenant HOST rather than by
DB schema. Running per-schema does mean the Nuxt purge is issued once
per tenant, which is what you want: each call carries that tenant's host.

``--prefixes`` needs none of this: ``CustomCache.clear_by_prefixes``
already scans both raw layouts (``{prefix}*`` and ``*:{prefix}*``)
precisely so a platform-wide clear covers every schema. The gap was
only ever in the surface path, which goes through the schema-scoped
``_make_pattern``.
"""

from __future__ import annotations

# The default backend is core.caches.CustomCache (see CACHES) —
# the proxy delegates its raw-key helpers (keys/delete_raw_keys/
# clear_by_prefixes) to it.
from django.core.cache import cache as cache_instance
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from core.cache.service import CacheService


class Command(BaseCommand):
    help = (
        "Purge cached keys. By default targets registered cache surfaces"
        " (recommended) across every active tenant schema. Pass"
        " --prefixes for raw-prefix disaster recovery."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "surfaces",
            nargs="*",
            type=str,
            help=(
                "Cache surface codes to purge (e.g. 'pay_way', 'shipping')."
                " If empty AND --all is not set, lists available surfaces."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Purge every non-Heavy surface (skips translations).",
        )
        parser.add_argument(
            "--schema",
            default=None,
            help=(
                "Limit the purge to one tenant schema. Defaults to every"
                " active non-public tenant."
            ),
        )
        parser.add_argument(
            "--public-only",
            action="store_true",
            help=(
                "Purge only the public schema (the platform control"
                " plane). Django cache keys are schema-prefixed, so this"
                " does NOT touch any tenant's keys."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be purged without removing keys.",
        )
        parser.add_argument(
            "--no-related",
            action="store_true",
            help="Do not auto-include surfaces marked as related.",
        )
        parser.add_argument(
            "--prefixes",
            nargs="*",
            type=str,
            default=None,
            help=(
                "Disaster-recovery escape hatch: raw key-prefix UNLINK"
                " against Django Redis (does NOT touch Nuxt SSR cache,"
                " bypasses the cache-surface registry). Use when the"
                " registry coverage is suspect or you need to nuke keys"
                " written by something outside the surface system."
            ),
        )

    def handle(self, *args, **options):
        if options["prefixes"]:
            self._raw_prefix_clear(options["prefixes"])
            return

        if not options["all"] and not options["surfaces"]:
            self._list_surfaces()
            return

        for schema in self._target_schemas(options):
            self.stdout.write(self.style.MIGRATE_HEADING(f"\nschema: {schema}"))
            with schema_context(schema):
                self._purge_one(options)

    def _target_schemas(self, options) -> list[str]:
        """Which schemas to purge, in order.

        Errors rather than silently purging nothing when ``--schema``
        names a tenant that does not exist — the old behaviour's real
        failure mode was reporting success having matched no keys.
        """
        from tenant.models import Tenant

        public = get_public_schema_name()

        if options["public_only"]:
            if options["schema"]:
                raise CommandError(
                    "--public-only and --schema are mutually exclusive."
                )
            return [public]

        if options["schema"]:
            if options["schema"] == public:
                return [public]
            exists = Tenant.objects.filter(
                schema_name=options["schema"], is_active=True
            ).exists()
            if not exists:
                raise CommandError(
                    f"No active tenant with schema "
                    f"{options['schema']!r}. Use --public-only for the "
                    f"platform control plane."
                )
            return [options["schema"]]

        schemas = list(
            Tenant.objects.filter(is_active=True)
            .exclude(schema_name=public)
            .order_by("schema_name")
            .values_list("schema_name", flat=True)
        )
        if not schemas:
            self.stdout.write(
                self.style.WARNING(
                    "No active tenants — falling back to the public schema."
                )
            )
            return [public]
        return schemas

    def _purge_one(self, options) -> None:
        if options["all"]:
            report = CacheService.purge_all(dry_run=options["dry_run"])
        else:
            report = CacheService.purge(
                options["surfaces"],
                dry_run=options["dry_run"],
                include_related=not options["no_related"],
            )

        prefix = "[dry-run] " if report.dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Purged {report.total_django} Django + "
                f"{report.total_nuxt} Nuxt + {report.total_gateway} feed "
                f"keys across {len(report.surfaces)} surface(s)"
            )
        )
        for surface in report.surfaces:
            line = (
                f"  {surface.code:25} django={surface.django_deleted}"
                f" nuxt={surface.nuxt_deleted}"
                f" blocked={surface.django_blocked + surface.nuxt_blocked}"
            )
            if surface.gateway_removed or surface.gateway_error:
                line += f" feeds={surface.gateway_removed}"
            if surface.django_error:
                line += f" django_error={surface.django_error}"
            if surface.nuxt_error:
                line += f" nuxt_error={surface.nuxt_error}"
            if surface.gateway_error:
                line += f" gateway_error={surface.gateway_error}"
            self.stdout.write(line)

    def _list_surfaces(self) -> None:
        from core.cache.registry import iter_surfaces

        self.stdout.write("Available cache surfaces:")
        for surface in iter_surfaces():
            danger = " [Heavy]" if surface.danger else ""
            self.stdout.write(f"  {surface.code:25} {surface.label}{danger}")
        self.stdout.write(
            "\nUsage: clear_cache <surface> [<surface> ...] [--dry-run]"
        )
        self.stdout.write("       clear_cache --all [--dry-run]")
        self.stdout.write(
            "       clear_cache --all --schema <tenant>   (one tenant)"
        )
        self.stdout.write(
            "       clear_cache --all --public-only       (control plane)"
        )

    def _raw_prefix_clear(self, prefixes: list[str]) -> None:
        self.stdout.write(
            self.style.WARNING(
                "Raw prefix mode (disaster recovery): bypassing the"
                " cache-surface registry. This does NOT purge Nuxt SSR"
                " cache and may match unintended keys — prefer surface"
                " codes unless you know what you're doing."
            )
        )
        try:
            results = cache_instance.clear_by_prefixes(prefixes)
            total = sum(results.values())
            for prefix, count in results.items():
                self.stdout.write(f"  {prefix}* -> {count} keys deleted")
            self.stdout.write(self.style.SUCCESS(f"Cleared {total} keys"))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Error: {exc!s}"))
