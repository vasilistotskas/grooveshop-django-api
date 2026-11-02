"""The editor's live preview is bound to the store it is asked on.

``POST builder/preview/resolve`` resolves unsaved working copies over the
store's current release. A staff token is minted in the public schema, so
what keeps it to one store is the role the person holds in the store the
request arrives on; and the release and documents it reads are the
schema's own tables. Both are checked here through the real host routing.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from builder import services
from builder.storefront import clear_catalogue_cache
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import (
    PlatformStaffToken,
    StaffTokenScope,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.builder import FakeCatalogue, document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


class _Publisher:
    """Who writes the fixtures: may do everything, as a superuser does."""

    pk = 1

    def has_perm(self, permission: str) -> bool:
        return True


setattr(_Publisher, PLATFORM_IDENTITY_ATTR, True)


@pytest.fixture(autouse=True)
def _storefront(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    clear_catalogue_cache()
    cache.clear()
    fake = FakeCatalogue()
    with (
        mock.patch("builder.storefront.requests.get", side_effect=fake.get),
        mock.patch("builder.services.feature_on", return_value=True),
    ):
        yield
    clear_catalogue_cache()


def _publish(schema: str, documents: dict[str, dict]) -> None:
    with schema_context(schema):
        for key, content in documents.items():
            services.save_draft(
                _Publisher(),
                key=key,
                draft=content,
                i18n={},
                expected_revision=0,
            )
        services.publish(_Publisher(), keys=list(documents))


@pytest.fixture
def staff_of_a(mt_tenant):
    """An ADMIN of store A only, with a builder token."""
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user = UserAccount.objects.create_user(
            email="mt-preview-admin@example.com",
            username="mtpreviewadmin",
            password="a-passw0rd!",
            is_staff=True,
        )
        UserTenantMembership.objects.create(
            user=user, tenant=mt_tenant, role=TenantMembershipRole.ADMIN
        )
        _instance, token = PlatformStaffToken.objects.create(
            user=user,
            expiry=timedelta(hours=1),
            scope=StaffTokenScope.BUILDER,
        )
    return token


def preview(tenant, token, body):
    return TenantClient(tenant).post(
        reverse("builder:preview-resolve"),
        body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"StaffBearer {token}",
    )


def test_a_token_of_store_a_is_refused_on_store_b(
    mt_tenant, mt_tenant_b, staff_of_a
):
    body = {"pageKey": "home", "documents": {}}

    on_a = preview(mt_tenant, staff_of_a, body)
    on_b = preview(mt_tenant_b, staff_of_a, body)

    assert on_a.status_code in (200, 404), on_a.content
    assert on_b.status_code in (401, 403), on_b.content
    assert b"store b" not in on_b.content


def test_a_preview_never_resolves_another_stores_release(
    mt_tenant, mt_tenant_b, staff_of_a
):
    _publish(
        mt_tenant.schema_name,
        {HOME: document(("b", section("banner@1", {"text": "store a"})))},
    )
    _publish(
        mt_tenant_b.schema_name,
        {
            HOME: document(("b", section("banner@1", {"text": "store b"}))),
            "page:b-only": document(
                ("b", section("banner@1", {"text": "only in b"}))
            ),
            "group:header": document(
                ("ann", section("announcement@1", {"text": "b header"}))
            ),
        },
    )

    # B's document keys in the body change nothing about whose release it is.
    response = preview(
        mt_tenant,
        staff_of_a,
        {
            "pageKey": "home",
            "documents": {
                "page:b-only": {
                    "draft": document(
                        ("b", section("banner@1", {"text": "copy"}))
                    ),
                    "i18n": {},
                }
            },
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["release"] == 1
    assert body["page"]["sections"]["b"]["settings"]["text"] == "store a"
    assert body["groups"] == {}
    assert b"store b" not in response.content
    assert b"b header" not in response.content


def test_a_page_only_the_other_store_has_is_not_found(
    mt_tenant, mt_tenant_b, staff_of_a
):
    _publish(
        mt_tenant_b.schema_name,
        {"page:b-only": document(("b", section("banner@1", {"text": "b"})))},
    )

    response = preview(
        mt_tenant, staff_of_a, {"pageKey": "b-only", "documents": {}}
    )

    assert response.status_code == 404, response.content
    assert response.json()["code"] == "page_not_published"


def test_a_copy_is_stored_in_neither_store(mt_tenant, mt_tenant_b, staff_of_a):
    from builder.models import BuilderDocument, BuilderVersion

    response = preview(
        mt_tenant,
        staff_of_a,
        {
            "pageKey": "home",
            "documents": {
                HOME: {"draft": document(("b", hero("unsaved"))), "i18n": {}}
            },
        },
    )

    assert response.status_code == 200, response.content
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            assert not BuilderDocument.objects.exists()
            assert not BuilderVersion.objects.exists()
