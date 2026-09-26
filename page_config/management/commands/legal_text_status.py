"""Report which tenants still lack a platform legal-text update.

A rollout (a data migration such as ``0032_privacy_server_logs_section``)
rewrites only the legal pages still carrying the platform's own text.
Every other page is left to its merchant and flagged in their admin.
This lists those pages across every tenant, so an operator can see who
has not acted and follow up. Read-only.

Usage:
    manage.py legal_text_status
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.models import Tenant


class Command(BaseCommand):
    help = "List legal pages that still lack a platform legal-text update."

    def handle(self, *args, **options):
        from page_config.defaults import pending_legal_reviews

        schemas = (
            Tenant.objects.exclude(schema_name=get_public_schema_name())
            .order_by("schema_name")
            .values_list("schema_name", flat=True)
        )
        behind = 0
        for schema_name in schemas:
            with schema_context(schema_name):
                pending = pending_legal_reviews()
                lines = [
                    f"[{schema_name}] {page.slug}: needs revision "
                    f"{update.revision} (page at "
                    f"{page.legal_text_revision or 0})"
                    for page, update in pending
                ]
            for line in lines:
                self.stdout.write(line)
            behind += bool(lines)

        if behind:
            self.stdout.write(
                self.style.WARNING(
                    f"{behind} tenant(s) have legal pages awaiting review."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Every tenant's legal pages are current.")
            )
