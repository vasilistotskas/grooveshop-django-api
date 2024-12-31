from django.core.management.base import BaseCommand

from core.caches import cache_instance


class Command(BaseCommand):
    help = "Clears cache"

    def handle(self, *args, **kwargs):
        try:
            cache_instance.clear()
            self.stdout.write(self.style.SUCCESS("Successfully cleared cache"))
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f"Error clearing cache: {exc!s}")
            )
