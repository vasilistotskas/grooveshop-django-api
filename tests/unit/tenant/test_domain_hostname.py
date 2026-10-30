"""``TenantDomain.domain`` is a bare lowercase host name.

Routing (the storefront's Host normalisation, ``tenant/resolve`` and the
tenant middleware) matches the stored text exactly, so a spelling that
could never match must not be saved by any write path.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command

from tenant.admin import TenantDomainAdmin
from tenant.models import TenantDomain
from tenant.provisioning import ensure_api_domain
from tenant.validators import validate_store_hostname
from tests.utils.staff import store_tenant

REJECTED = [
    ("Shop.Example.com", "domain_not_lowercase"),
    ("shop.example.com:8000", "domain_has_port"),
    ("shop.example.com.", "domain_trailing_dot"),
    ("https://shop.example.com", "domain_has_scheme_or_path"),
    ("shop.example.com/path", "domain_has_scheme_or_path"),
    ("192.168.1.10", "domain_is_ip_address"),
    ("::1", "domain_is_ip_address"),
    ("[2001:db8::1]", "domain_is_ip_address"),
    ("localhost", "domain_single_label"),
    (".webside.gr", "domain_invalid_label"),
    ("shop..example.com", "domain_invalid_label"),
    ("-shop.example.com", "domain_invalid_label"),
    ("shop-.example.com", "domain_invalid_label"),
    ("sh_op.example.com", "domain_invalid_label"),
    ("shop example.com", "domain_invalid_label"),
    (f"{'a' * 64}.example.com", "domain_invalid_label"),
    (f"{'.'.join(['a' * 60] * 4)}.example.com", "domain_too_long"),
]

VALID = [
    "webside.gr",
    "api.webside.gr",
    "demo.grooveshop.space",
    "api-demo.grooveshop.space",
    "xn--kxae4bafwg.xn--pxaix.gr",
    "a1.example",
    f"{'a' * 63}.example.com",
]


@pytest.mark.parametrize(("value", "code"), REJECTED)
def test_validator_rejects_each_shape_with_its_code(value, code):
    with pytest.raises(ValidationError) as excinfo:
        validate_store_hostname(value)

    assert excinfo.value.code == code


@pytest.mark.parametrize("value", VALID)
def test_validator_accepts_real_host_names(value):
    validate_store_hostname(value)


@pytest.mark.django_db
class TestEveryWritePath:
    @pytest.fixture
    def tenant(self):
        return store_tenant("hostname_store", name="Hostname store")

    def test_full_clean_rejects(self, tenant):
        row = TenantDomain(domain="Shop.Example.com", tenant=tenant)

        with pytest.raises(ValidationError) as excinfo:
            row.full_clean()

        assert excinfo.value.error_dict["domain"][0].code == (
            "domain_not_lowercase"
        )

    def test_admin_form_rejects(self, tenant, rf):
        request = rf.get("/")
        request.user = get_user_model()(is_superuser=True, is_active=True)
        form_class = TenantDomainAdmin(
            TenantDomain, admin_site=admin.site
        ).get_form(request)
        form = form_class(
            data={
                "domain": "https://shop.example.com",
                "tenant": tenant.pk,
                "is_primary": True,
            }
        )

        assert not form.is_valid()
        assert form.errors.as_data()["domain"][0].code == (
            "domain_has_scheme_or_path"
        )

    def test_create_rejects(self, tenant):
        with pytest.raises(ValidationError):
            TenantDomain.objects.create(
                domain="shop.example.com:8000", tenant=tenant
            )

        assert not TenantDomain.objects.filter(
            domain="shop.example.com:8000"
        ).exists()

    def test_get_or_create_rejects(self, tenant):
        with pytest.raises(ValidationError):
            TenantDomain.objects.get_or_create(
                domain="Shop.example.com",
                tenant=tenant,
                defaults={"is_primary": False},
            )

    def test_resave_with_a_bad_value_rejects(self, tenant):
        row = TenantDomain.objects.create(
            domain="shop.example.com", tenant=tenant
        )
        row.domain = "Shop.example.com"

        with pytest.raises(ValidationError):
            row.save()

    def test_bootstrap_platform_rejects(self):
        with pytest.raises(ValidationError):
            call_command("bootstrap_platform", domain="Platform.example.com")

    def test_ensure_api_domain_derives_a_valid_host(self, tenant):
        TenantDomain.objects.create(domain="shop.example.com", tenant=tenant)

        assert ensure_api_domain(tenant) == "api.shop.example.com"
