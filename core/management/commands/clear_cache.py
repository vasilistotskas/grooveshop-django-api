from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from core.caches import cache_instance


class Command(BaseCommand):
    help = "Clear cached keys by prefix (default: Django + Nuxt cache)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefixes",
            nargs="*",
            type=str,
            default=None,
            help=(
                "Key prefixes to clear (e.g. 'redis:1:' 'cache:'). "
                "Defaults to settings.CACHE_CLEAR_PREFIXES."
            ),
        )

    def handle(self, *args, **options):
        prefixes = options["prefixes"]
        if prefixes is None:
            prefixes = getattr(settings, "CACHE_CLEAR_PREFIXES", [])

        if not prefixes:
            self.stderr.write(
                self.style.ERROR(
                    "No prefixes configured. Set CACHE_CLEAR_PREFIXES "
                    "in settings or pass --prefixes."
                )
            )
            return

        self.stdout.write(
            f"Clearing cache keys for prefixes: {', '.join(prefixes)}"
        )

        try:
            results = cache_instance.clear_by_prefixes(prefixes)
            total = sum(results.values())

            for prefix, count in results.items():
                self.stdout.write(f"  {prefix}* -> {count} keys deleted")

            self.stdout.write(
                self.style.SUCCESS(f"Successfully cleared {total} cache keys")
            )
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(f"Error clearing cache: {exc!s}")
            )
