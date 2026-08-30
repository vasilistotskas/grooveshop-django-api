"""A row created in the tenant schema is invisible in public — the
foundational invariant of schema-per-tenant multi-tenancy (the audit's
"zero cross-tenant data isolation tests — never asserts A writes -> B
can't see" gap).

``product`` is TENANT_APPS-only, so ``product_product`` does not exist
in the public schema's search_path AT ALL (verified: a fresh build has
NO such table there). This is a STRONGER isolation guarantee than "the
row is filtered out" — the whole table is out of reach from public,
so the query itself fails rather than silently returning nothing.

``country``/``region`` (SHARED_APPS-only) show the mirror case: their
tables exist ONLY in public, but a tenant schema's ``search_path`` is
``"{schema}, public"`` (falls through to public for anything the
tenant schema doesn't have), so they ARE reachable from inside a
tenant — by design, since reference data like this is meant to be
shared platform-wide.
"""

from __future__ import annotations

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context


@pytest.mark.django_db
def test_product_written_in_tenant_schema_is_unreachable_from_public(
    mt_tenant,
):
    from product.factories.product import ProductFactory
    from product.models.product import Product

    with schema_context(mt_tenant.schema_name):
        product = ProductFactory()
        product_id = product.id
        assert Product.objects.filter(id=product_id).exists()

    with schema_context("public"):
        # A savepoint, not the outer test transaction: the query is
        # EXPECTED to raise, and without one the aborted transaction
        # would poison every ORM call for the rest of this test.
        with pytest.raises(ProgrammingError, match="does not exist"):
            with transaction.atomic():
                Product.objects.filter(id=product_id).exists()

    # Still there, unaffected, back in its own schema.
    with schema_context(mt_tenant.schema_name):
        assert Product.objects.filter(id=product_id).exists()


@pytest.mark.django_db
def test_shared_reference_data_falls_through_to_public_from_a_tenant(
    mt_tenant,
):
    """The mirror case: SHARED_APPS-only data (country/region) is
    reachable from inside a tenant schema via the search_path
    fallback — by design, since it is meant to be shared platform-wide,
    not per-tenant isolated."""
    from country.models import Country

    with schema_context("public"):
        Country.objects.get_or_create(
            alpha_2="ZZ",
            defaults={"alpha_3": "ZZZ", "phone_code": 1},
        )

    with schema_context(mt_tenant.schema_name):
        assert Country.objects.filter(alpha_2="ZZ").exists(), (
            "a public-schema Country row was unreachable from the "
            "tenant schema — expected search_path fallthrough to public"
        )
