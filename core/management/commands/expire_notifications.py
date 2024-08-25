import importlib

from django.core.management.base import BaseCommand
from django.utils import timezone as tz


class Command(BaseCommand):
    help = "Delete or archive expired notifications"

    def handle(self, *args, **kwargs):
        notification = importlib.import_module("notification.models.notification").Notification
        now = tz.now()
        expired_notifications = notification.objects.filter(expiry_date__lt=now)
        expired_notifications.delete()
        self.stdout.write(self.style.SUCCESS("Successfully deleted expired notifications"))
