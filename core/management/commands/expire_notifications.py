import importlib
from contextlib import nullcontext as _nullcontext
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.management.tenant_mixin import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = "Delete or archive expired notifications"

    # Notification is a TENANT_APPS model — the table does not exist in
    # the public schema. Manual runs must name their target; the
    # scheduled path (fanout_clear_expired_notifications ->
    # clear_expired_notifications_task under TenantTask) is already
    # inside a tenant context and passes the guard without flags.
    require_tenant_scope = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Delete notifications older than this number of days",
        )
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        from django_tenants.utils import schema_context

        for schema in self.get_tenant_schemas(options):
            if schema:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(f"\n>>> Tenant: {schema}")
                )
            with schema_context(schema) if schema else _nullcontext():
                self._handle_for_schema(*args, **options)

    def _handle_for_schema(self, *args, **options):
        from django.db import transaction

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

        with transaction.atomic():
            if not expired_notifications.exists():
                self.stdout.write(
                    self.style.WARNING(f"No {message} found to delete")
                )
                return

            deleted_count, _deleted_details = expired_notifications.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully deleted {deleted_count} {message}"
            )
        )
