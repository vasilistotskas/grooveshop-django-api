"""
Management command to clear Django's translation cache.
Useful after manually editing .po files or when translations aren't updating.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import trans_real


class Command(BaseCommand):
    help = "Clear Django's translation cache to force reload of .mo files"

    def handle(self, *args, **options):
        """Clear translation cache and force reload."""
        trans_real._translations = {}
        trans_real._default = None
        trans_real._active = None
        self.stdout.write(
            self.style.SUCCESS("✓ Cleared translation catalog cache")
        )

        self.stdout.write(
            self.style.SUCCESS(
                "\nTranslation cache cleared successfully! "
                "Translations will be reloaded on next request."
            )
        )
