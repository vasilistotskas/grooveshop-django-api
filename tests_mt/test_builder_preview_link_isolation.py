"""A share-preview link belongs to the store that made it.

``BuilderPreviewLink`` is a tenant table, so a token is looked up in the
schema of the host it is opened on: a link of store A finds nothing on
store B, and the sweep that removes expired links works one store at a time.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context

from builder import preview_links, services
from builder.models import BuilderPreviewLink
from builder.storefront import clear_catalogue_cache
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tests.utils.builder import FakeCatalogue, document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


class _Staff:
    pk = 1

    def has_perm(self, permission: str) -> bool:
        return True


setattr(_Staff, PLATFORM_IDENTITY_ATTR, True)


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


def _share(schema: str, text: str) -> str:
    with schema_context(schema):
        services.save_draft(
            _Staff(),
            key=HOME,
            draft=document(("b", section("banner@1", {"text": text}))),
            i18n={},
            expected_revision=0,
        )
        _link, token = preview_links.create(_Staff(), keys=[HOME])
    return token


def _open(tenant, token):
    return TenantClient(tenant).get(
        reverse(
            "builder:preview-shared",
            kwargs={"token": token, "page_key": "home"},
        )
    )


def test_a_link_opens_on_its_own_store_only(mt_tenant, mt_tenant_b):
    token = _share(mt_tenant.schema_name, "store a")

    on_a = _open(mt_tenant, token)
    on_b = _open(mt_tenant_b, token)

    assert on_a.status_code == 200, on_a.content
    assert on_a.json()["page"]["sections"]["b"]["settings"]["text"] == "store a"
    assert on_b.status_code == 404
    assert on_b.json()["code"] == "preview_link_expired"
    assert b"store a" not in on_b.content


def test_the_sweep_acts_on_one_store_at_a_time(mt_tenant, mt_tenant_b):
    for tenant, text in ((mt_tenant, "a"), (mt_tenant_b, "b")):
        _share(tenant.schema_name, text)
        with schema_context(tenant.schema_name):
            BuilderPreviewLink.objects.update(
                expires_at=timezone.now() - timedelta(days=1)
            )
    with schema_context(mt_tenant.schema_name):
        BuilderPreviewLink.objects.create(
            token_hash="f" * 64,
            snapshots={},
            expires_at=timezone.now() + timedelta(days=1),
        )

    with schema_context(mt_tenant.schema_name):
        assert preview_links.purge_expired() == 1
        assert BuilderPreviewLink.objects.count() == 1
    with schema_context(mt_tenant_b.schema_name):
        assert BuilderPreviewLink.objects.count() == 1
