"""Every page of the platform admin opens, on the real public schema.

The platform site copies the store admin's ModelAdmin classes, and a
copied admin can read a table that exists only in store schemas. The
main suite cannot see that: it runs every app in one schema. On
production ``GET /admin/user/useraccount/2/change/`` answered 500
(``relation "loyalty_points_transaction" does not exist``, 2026-10-15),
and deleting a platform identity failed the same way on
``account_emailaddress`` (2026-10-16).

Walks every model registered on the platform site and opens its list,
its add form and the change form of one row, as a platform superuser on
the platform host, so a new admin that reaches into a store-only table
fails here first.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from admin.platform_site import platform_admin_site
from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

pytestmark = pytest.mark.django_db

PLATFORM_DOMAIN = "platform-mt.test"
URLCONF = settings.PUBLIC_SCHEMA_URLCONF


@pytest.fixture
def platform_client(mt_public_tenant):
    from tenant.models import TenantDomain
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
        operator, _created = UserAccount.objects.get_or_create(
            email="mt-platform-operator@example.com",
            defaults={
                "username": "mtplatformoperator",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        client = TenantClient(mt_public_tenant)
        client.force_login(operator, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


def _url(name: str, *args) -> str:
    return reverse(f"platform_admin:{name}", args=args, urlconf=URLCONF)


def _pages():
    for model, model_admin in platform_admin_site._registry.items():
        opts = model._meta
        yield pytest.param(
            model, model_admin, id=f"{opts.app_label}.{opts.model_name}"
        )


@pytest.mark.parametrize(("model", "model_admin"), list(_pages()))
def test_every_platform_model_page_opens(platform_client, model, model_admin):
    opts = model._meta
    prefix = f"{opts.app_label}_{opts.model_name}"

    response = platform_client.get(_url(f"{prefix}_changelist"))
    assert response.status_code == 200, response.status_code

    if model_admin.has_add_permission(_request()):
        response = platform_client.get(_url(f"{prefix}_add"))
        assert response.status_code == 200, response.status_code

    with schema_context(get_public_schema_name()):
        row = model._default_manager.order_by("pk").first()
    if row is not None:
        response = platform_client.get(_url(f"{prefix}_change", row.pk))
        assert response.status_code == 200, response.status_code


def test_a_platform_identity_can_be_deleted(platform_client, mt_public_tenant):
    """Its store role goes with it; nothing reaches a store-only table."""
    from tenant.models import TenantMembershipRole, UserTenantMembership
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        leaver = UserAccount.objects.create_user(
            email="mt-leaver@example.com",
            username="mtleaver",
            password="passw0rd!",
            is_staff=True,
        )
        UserTenantMembership.objects.create(
            user=leaver,
            tenant=mt_public_tenant,
            role=TenantMembershipRole.ADMIN,
        )

    url = _url("user_useraccount_delete", leaver.pk)
    assert platform_client.get(url).status_code == 200

    response = platform_client.post(url, {"post": "yes"})

    assert response.status_code == 302
    with schema_context(get_public_schema_name()):
        assert not UserAccount.objects.filter(pk=leaver.pk).exists()
        assert not UserTenantMembership.objects.filter(
            user_id=leaver.pk
        ).exists()


def _request():
    """A request for permission questions: a platform superuser."""
    from django.test import RequestFactory

    from user.models import UserAccount

    request = RequestFactory().get("/admin/")
    with schema_context(get_public_schema_name()):
        request.user = UserAccount.objects.get(
            email="mt-platform-operator@example.com"
        )
    return request
