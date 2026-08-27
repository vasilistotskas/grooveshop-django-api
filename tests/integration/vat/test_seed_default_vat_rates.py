"""A fresh tenant needs usable VAT rates; an existing one must not change.

``vat`` is TENANT_APPS-only and had no data migration, so a newly
provisioned tenant started with an empty table (verified: the staging
tenant ``aurora``). That blocked the product WRITE API outright, because
both product serializers declare ``vat`` as an explicitly-declared
relation — which is required regardless of the model's ``null=True`` —
against a queryset with nothing in it.

The mirror risk is the one that matters on deploy: these rows are LIVE
on existing tenants and are referenced by priced products, so a seeder
that rewrote them would change what customers are charged and what
appears on already-issued invoices.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from vat.models import Vat

MIGRATION = "vat.migrations.0007_seed_default_vat_rates"


@pytest.fixture
def seed():
    module = importlib.import_module(MIGRATION)

    class _SchemaEditor:
        class connection:
            alias = "default"

    def _run():
        from django.apps import apps

        module.seed_vat_rates(apps, _SchemaEditor)

    return _run


@pytest.mark.django_db
class TestFreshTenantSeeding:
    def test_seeds_the_mainland_greek_rates(self, seed):
        Vat.objects.all().delete()

        seed()

        assert set(Vat.objects.values_list("value", flat=True)) == {
            Decimal("24.0"),
            Decimal("13.0"),
            Decimal("6.0"),
            Decimal("0.0"),
        }

    def test_every_seeded_rate_is_accepted_by_mydata(self, seed):
        """An unlisted rate raises when an invoice is submitted.

        ``order/mydata/builder.py`` maps rate -> AADE vatCategory and
        treats anything missing as bad master data, so seeding a rate it
        does not know would plant a failure that only surfaces once a
        merchant enables myDATA.
        """
        from order.mydata.builder import _vat_category

        Vat.objects.all().delete()
        seed()

        for value in Vat.objects.values_list("value", flat=True):
            # Raises ValueError on an unmapped rate — which is exactly
            # what production's legacy 23.0 row would do today.
            assert _vat_category(Decimal(value)) is not None

    def test_is_idempotent(self, seed):
        Vat.objects.all().delete()

        seed()
        seed()
        seed()

        assert Vat.objects.count() == 4


@pytest.mark.django_db
class TestExistingTenantIsUntouched:
    def test_does_not_rewrite_or_remove_a_live_rate(self, seed):
        """Production runs a single 23.0 row that priced products point at.

        Rewriting or deleting it would reprice live products and
        invalidate issued invoices, so the seeder must only ever ADD.
        """
        Vat.objects.all().delete()
        legacy = Vat.objects.create(value=Decimal("23.0"))

        seed()

        legacy.refresh_from_db()
        assert legacy.value == Decimal("23.0")
        assert Vat.objects.filter(value=Decimal("23.0")).count() == 1
        # The correct standard rate is now available to migrate TO.
        assert Vat.objects.filter(value=Decimal("24.0")).exists()

    def test_does_not_duplicate_an_already_seeded_rate(self, seed):
        Vat.objects.all().delete()
        Vat.objects.create(value=Decimal("24.0"))

        seed()

        assert Vat.objects.filter(value=Decimal("24.0")).count() == 1
        assert Vat.objects.count() == 4


@pytest.mark.django_db
class TestProductWriteApiSurvivesWithoutVatRows:
    """The serializer/model mismatch that seeding alone would not fix."""

    def test_vat_is_optional_on_both_product_serializers(self):
        from product.serializers.product import (
            ProductSerializer,
            ProductWriteSerializer,
        )

        for serializer_class in (ProductSerializer, ProductWriteSerializer):
            field = serializer_class().fields["vat"]
            assert not field.required, (
                f"{serializer_class.__name__}.vat is required, so a tenant "
                "with no Vat rows cannot create a product at all"
            )
            assert field.allow_null, (
                f"{serializer_class.__name__}.vat must accept null to match "
                "Product.vat (null=True, SET_NULL)"
            )
