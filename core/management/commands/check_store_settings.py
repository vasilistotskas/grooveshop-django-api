from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context
from extra_settings.models import Setting
from extra_settings.utils import import_function

from tenant.models import Tenant


class Command(BaseCommand):
    help = (
        "Fail when any store's saved setting is refused by the validator "
        "its declaration names (settings.EXTRA_SETTINGS_DEFAULTS)"
    )

    def handle(self, *args: Any, **options: Any) -> None:
        validators = {
            entry["name"]: import_function(entry["validator"])
            for entry in settings.EXTRA_SETTINGS_DEFAULTS
            if entry.get("validator")
        }
        refused: list[tuple[str, str]] = []
        for tenant in Tenant.objects.order_by("schema_name"):
            with schema_context(tenant.schema_name):
                for row in Setting.objects.filter(name__in=validators):
                    if not validators[row.name](row.value):
                        refused.append((tenant.schema_name, row.name))

        if refused:
            # The name and the store, never the value: a setting may hold
            # a credential.
            for schema, name in refused:
                self.stderr.write(f"{schema}: {name} fails its validator")
            raise CommandError(
                f"{len(refused)} saved setting(s) are refused by their "
                "validator. Fix them, or ship the shape change with a data "
                "migration of the stored values."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Every saved setting passes its validator "
                f"({len(validators)} validated names)."
            )
        )
