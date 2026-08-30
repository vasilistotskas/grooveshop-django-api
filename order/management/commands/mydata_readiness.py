"""Pre-flight gate for enabling myDATA e-invoicing on a tenant.

Greek standard VAT is 24% (23% stopped being valid in June 2016), and
myDATA B2B e-invoicing becomes mandatory during 2026. Production
webside was found running products at the stale 23% rate — fixed, but
nothing caught it automatically. This command is the check an operator
runs BEFORE flipping ``MYDATA_ENABLED`` on for a tenant, and CI/ops can
run it on a schedule since it exits non-zero on any violation.

Reports per tenant:

* Vat rows whose ``value`` has no myDATA ``vatCategory`` mapping
  (``vat.constants.MYDATA_SUPPORTED_VAT_RATES`` — the same set the
  ``Vat`` model itself now rejects at save time, so this mostly
  catches rows written before that validator existed).
* Products with ``vat=NULL`` — priced at 0% VAT and invoiced wrong.
* Product count per VAT rate, for a quick sanity read of the mix.

Both ``vat`` and ``product`` are TENANT_APPS-only tables, so this
follows the same ``--tenant``/``--all-tenants`` contract as
``expire_notifications``/``reconcile_acs_cod``.
"""

from __future__ import annotations

from contextlib import nullcontext as _nullcontext

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from core.management.tenant_mixin import TenantCommandMixin
from vat.constants import MYDATA_SUPPORTED_VAT_RATES


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Report myDATA readiness (unmapped VAT rates, vat=NULL "
        "products) for a tenant or all tenants. Exits non-zero if any "
        "violation is found."
    )

    require_tenant_scope = True

    def add_arguments(self, parser):
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        from django_tenants.utils import schema_context

        total_unmapped = 0
        total_null_vat = 0

        for schema in self.get_tenant_schemas(options):
            if schema:
                self.stdout.write(
                    self.style.MIGRATE_HEADING(f"\n>>> Tenant: {schema}")
                )
            with schema_context(schema) if schema else _nullcontext():
                unmapped, null_vat = self._report_for_schema()
                total_unmapped += unmapped
                total_null_vat += null_vat

        if total_unmapped or total_null_vat:
            raise CommandError(
                f"myDATA readiness FAILED: {total_unmapped} unmapped VAT "
                f"rate(s), {total_null_vat} product(s) with vat=NULL "
                "across the checked tenant(s)."
            )

        self.stdout.write(
            self.style.SUCCESS("\nmyDATA readiness OK — no violations found.")
        )

    def _report_for_schema(self) -> tuple[int, int]:
        from product.models.product import Product
        from vat.models import Vat

        unmapped_qs = Vat.objects.exclude(value__in=MYDATA_SUPPORTED_VAT_RATES)
        unmapped_count = unmapped_qs.count()
        if unmapped_count:
            self.stdout.write(
                self.style.ERROR(
                    f"  ✗ {unmapped_count} Vat row(s) with a rate myDATA "
                    "cannot map:"
                )
            )
            for value in unmapped_qs.values_list("value", flat=True).order_by(
                "value"
            ):
                self.stdout.write(f"      - {value}%")
        else:
            self.stdout.write(
                self.style.SUCCESS("  ✓ All Vat rates are myDATA-recognised")
            )

        null_vat_count = Product.objects.filter(vat__isnull=True).count()
        if null_vat_count:
            self.stdout.write(
                self.style.ERROR(
                    f"  ✗ {null_vat_count} product(s) with vat=NULL "
                    "(priced/invoiced at 0%)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("  ✓ No products with vat=NULL")
            )

        counts = (
            Product.objects.exclude(vat__isnull=True)
            .values("vat__value")
            .order_by("vat__value")
            .annotate(count=Count("id"))
        )
        if counts:
            self.stdout.write("  Product count per VAT rate:")
            for row in counts:
                self.stdout.write(
                    f"      - {row['vat__value']}%: {row['count']}"
                )

        return unmapped_count, null_vat_count
