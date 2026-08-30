"""Tests for the ``mydata_readiness`` pre-flight command.

Deliberately writes the unmapped-rate Vat rows via the bare ORM
(``.objects.create``), bypassing ``full_clean()`` — this command exists
precisely to catch rows that predate ``validate_mydata_vat_rate``
(vat/models.py), or that were inserted by something other than a form/
serializer (a script, a fixture, a pre-validator legacy row).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django_tenants.utils import schema_context

from product.factories.product import ProductFactory
from vat.factories import VatFactory
from vat.models import Vat


@pytest.mark.django_db
class TestMydataReadinessTenantScopeGuard:
    def test_refuses_public_schema_context(self):
        with (
            schema_context("public"),
            pytest.raises(CommandError, match="public schema context"),
        ):
            call_command("mydata_readiness")

    def test_tenant_context_passes_without_flags_when_clean(self):
        """The Celery fanout path: call_command inside a tenant context
        must keep working flag-less, same as expire_notifications."""
        Vat.objects.all().delete()
        with schema_context("mydata_readiness_scope_tenant"):
            call_command("mydata_readiness")


@pytest.mark.django_db
class TestMydataReadinessReport:
    def test_passes_when_every_rate_is_mapped_and_no_product_lacks_vat(
        self, capsys
    ):
        Vat.objects.all().delete()
        vat = VatFactory(value=Decimal("24.0"))
        ProductFactory(vat=vat)

        with schema_context("mydata_readiness_scope_tenant"):
            call_command("mydata_readiness")

        assert "OK" in capsys.readouterr().out

    def test_raises_when_a_vat_rate_is_unmapped(self):
        Vat.objects.all().delete()
        # Bypasses full_clean() on purpose — see module docstring.
        Vat.objects.create(value=Decimal("23.0"))

        with (
            schema_context("mydata_readiness_scope_tenant"),
            pytest.raises(CommandError, match="1 unmapped VAT rate"),
        ):
            call_command("mydata_readiness")

    def test_raises_when_a_product_has_no_vat(self):
        Vat.objects.all().delete()
        ProductFactory(vat=None)

        with (
            schema_context("mydata_readiness_scope_tenant"),
            pytest.raises(CommandError, match="1 product\\(s\\) with vat=NULL"),
        ):
            call_command("mydata_readiness")

    def test_reports_product_count_per_vat_rate(self, capsys):
        Vat.objects.all().delete()
        vat = VatFactory(value=Decimal("13.0"))
        ProductFactory(vat=vat)
        ProductFactory(vat=vat)

        with schema_context("mydata_readiness_scope_tenant"):
            call_command("mydata_readiness")

        out = capsys.readouterr().out
        assert "13.0%: 2" in out
