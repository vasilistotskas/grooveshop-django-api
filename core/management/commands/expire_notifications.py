import importlib
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete or archive expired notifications"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Delete notifications older than this number of days",
        )

    def handle(self, *args, **options):
        notification = importlib.import_module(
            "notification.models.notification"
        ).Notification

        days = options.get("days")

        if days is not None:
            cutoff_date = timezone.now() - timedelta(days=days)
            expired_notifications = notification.objects.filter(
                created_at__lt=cutoff_date
            )
            message = f"notifications older than {days} days"
        else:
            expired_notifications = notification.objects.filter(
                expiry_date__lt=timezone.now()
            ).exclude(expiry_date__isnull=True)
            message = "expired notifications"

        count = expired_notifications.count()

        if count == 0:
            self.stdout.write(
                self.style.WARNING(f"No {message} found to delete")
            )
            return

        deleted_count, deleted_details = expired_notifications.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully deleted {deleted_count} {message}"
            )
        )
