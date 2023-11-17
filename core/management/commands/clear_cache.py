from django.core.cache import cache
from django.core.management.base import BaseCommand

from core.caches import cache_instance


class Command(BaseCommand):
    help = "Clears cache"

    def handle(self, *args, **kwargs):
        cache.clear()
        cache_instance.clear()
        self.stdout.write(self.style.SUCCESS("Successfully cleared cache"))
