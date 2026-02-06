"""
Management command to clear Django's translation cache.
Useful after manually editing .po files or when translations aren't updating.

Bumps the shared translation version in Redis so that all pods in a
multi-replica deployment reload their in-memory gettext catalogs.
"""

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand

from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY
from core.rosetta_storage import _reload_translations


class Command(BaseCommand):
    help = "Clear Django's translation cache to force reload of .mo files"

    def handle(self, *args, **options):
        _reload_translations()
        self.stdout.write(
            self.style.SUCCESS("Cleared local translation catalog")
        )

        try:
            cache.set(TRANSLATION_VERSION_CACHE_KEY, time.time(), timeout=None)
            self.stdout.write(
                self.style.SUCCESS(
                    "Bumped shared translation version — "
                    "all pods will reload on next request"
                )
            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f"Could not bump shared version: {e}")
            )
