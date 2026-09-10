"""``Tenant.is_platform_storefront`` and the per-store SEO attribution.

The platform's own storefront used to be recognised by comparing a
tenant's primary domain with ``NUXT_PUBLIC_BASE_URL`` (storefront) and
``NUXT_BASE_URL`` (Django) — which forced a platform env value to carry
tenant #1's hostname, and that value rode into every tenant's
serialized runtime config. It is a row flag now, with the author and
site-verification tokens as per-store fields next to it.

Two things are worth pinning:

**Optional, not read-only, on the public serializer.** drf-spectacular
marks read-only fields as required, so a frontend-first deploy would
reject every ``/tenant/resolve`` from a backend that predates the field
and 503 the whole storefront (see ``test_openai_pixel_id``).

**One platform storefront, enforced by the database.** Two flagged
stores would both render the bundled brand.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from tenant.models import Tenant
from tenant.serializers import TenantConfigSerializer

NEW_FIELDS = (
    "is_platform_storefront",
    "seo_author",
    "google_site_verification",
    "pinterest_domain_verify",
)


class TestPublicSerializerShape:
    @pytest.mark.parametrize("name", NEW_FIELDS)
    def test_field_is_optional_for_a_frontend_first_deploy(self, name):
        field = TenantConfigSerializer().fields[name]
        assert field.required is False
        assert field.read_only is False


def _tenant(slug: str, **overrides) -> Tenant:
    tenant = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        **overrides,
    )
    tenant.auto_create_schema = False
    tenant.save()
    return tenant


@pytest.mark.django_db
class TestSinglePlatformStorefront:
    def test_a_second_flagged_tenant_is_refused_by_the_database(self):
        _tenant("platform-store-a", is_platform_storefront=True)

        with pytest.raises(IntegrityError), transaction.atomic():
            _tenant("platform-store-b", is_platform_storefront=True)

    def test_unflagged_tenants_are_unconstrained(self):
        _tenant("plain-store-a")
        _tenant("plain-store-b")

        assert Tenant.objects.filter(is_platform_storefront=True).count() == 0

    def test_seo_fields_default_empty_and_round_trip(self):
        tenant = _tenant(
            "seo-store",
            seo_author="Store Owner",
            google_site_verification="gsv-token",
            pinterest_domain_verify="pin-token",
        )

        data = TenantConfigSerializer(tenant).data

        assert data["seo_author"] == "Store Owner"
        assert data["google_site_verification"] == "gsv-token"
        assert data["pinterest_domain_verify"] == "pin-token"
        assert data["is_platform_storefront"] is False

        plain = _tenant("plain-seo-store")
        assert TenantConfigSerializer(plain).data["seo_author"] == ""
