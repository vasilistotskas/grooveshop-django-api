import time

from django.core.management.base import BaseCommand, CommandError
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Wait for the database to become available"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Maximum time to wait in seconds (default: 30, 0 for unlimited)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=1,
            help="Seconds between retries (default: 1)",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        interval = options["interval"]

        self.stdout.write("Waiting for database...")
        start_time = time.monotonic()

        while True:
            try:
                self.check(databases=["default"])
                break
            except OperationalError:
                elapsed = time.monotonic() - start_time
                if timeout and elapsed >= timeout:
                    raise CommandError(
                        f"Database unavailable after {timeout} seconds"
                    )
                self.stdout.write(
                    f"Database unavailable, waiting {interval} second(s)..."
                )
                time.sleep(interval)

        self.stdout.write(self.style.SUCCESS("Database available!"))
