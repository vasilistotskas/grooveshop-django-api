from __future__ import annotations

from django.core.management.base import BaseCommand

from core.cache.service import CacheService
from core.caches import cache_instance


class Command(BaseCommand):
    help = (
        "Purge cached keys. By default targets registered cache surfaces"
        " (recommended). Pass --prefixes for legacy raw-prefix clearing."
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
                "Legacy mode: raw key-prefix UNLINK against Django Redis"
                " (does NOT touch Nuxt cache). Use only for disaster"
                " recovery."
            ),
        )

    def handle(self, *args, **options):
        if options["prefixes"]:
            self._legacy_prefix_clear(options["prefixes"])
            return

        if options["all"]:
            report = CacheService.purge_all(dry_run=options["dry_run"])
        elif options["surfaces"]:
            report = CacheService.purge(
                options["surfaces"],
                dry_run=options["dry_run"],
                include_related=not options["no_related"],
            )
        else:
            self._list_surfaces()
            return

        prefix = "[dry-run] " if report.dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Purged {report.total_django} Django + "
                f"{report.total_nuxt} Nuxt keys "
                f"across {len(report.surfaces)} surface(s)"
            )
        )
        for surface in report.surfaces:
            self.stdout.write(
                f"  {surface.code:25} django={surface.django_deleted}"
                f" nuxt={surface.nuxt_deleted}"
                f" blocked={surface.django_blocked + surface.nuxt_blocked}"
                + (
                    f" nuxt_error={surface.nuxt_error}"
                    if surface.nuxt_error
                    else ""
                )
            )

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

    def _legacy_prefix_clear(self, prefixes: list[str]) -> None:
        self.stdout.write(
            self.style.WARNING(
                "Legacy mode: clearing by raw prefix. This does NOT"
                " purge Nuxt SSR cache and may match unintended keys."
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
