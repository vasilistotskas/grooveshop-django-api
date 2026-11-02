"""The media library stays inside its store: an image is deduplicated against
the store's own library only, a store's uploads are imported by that store
alone, and a share link's media is not reachable from another store."""

from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context
from PIL import Image

from builder import media_services, preview_links, services
from builder.errors import BuilderError
from builder.models import MediaAsset
from builder.storefront import clear_catalogue_cache
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tests.utils.builder import FakeCatalogue, document, hero

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


def png() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return SimpleUploadedFile("a.png", buffer.getvalue(), "image/png")


def test_the_same_image_is_an_asset_of_each_store(mt_tenant, mt_tenant_b):
    stored = {}
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            stored[tenant.schema_name] = media_services.create_asset(
                _Staff(), upload=png()
            )

    assert all(item.created for item in stored.values())
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            assert MediaAsset.objects.count() == 1


def test_a_store_does_not_import_another_stores_upload(mt_tenant, mt_tenant_b):
    with schema_context(mt_tenant.schema_name):
        stored = media_services.create_asset(_Staff(), upload=png())
        path = f"/media/{mt_tenant.schema_name}/{stored.asset.file.name}"

    with schema_context(mt_tenant_b.schema_name):
        with pytest.raises(BuilderError) as refused:
            media_services.import_url(_Staff(), url=path)

    assert refused.value.detail["code"] == "import_other_store"


def test_a_links_media_is_not_reachable_from_another_store(
    mt_tenant, mt_tenant_b
):
    with schema_context(mt_tenant.schema_name):
        asset = media_services.create_asset(_Staff(), upload=png()).asset
        services.save_draft(
            _Staff(),
            key=HOME,
            draft=document(("h", hero("T", mediaId=asset.pk))),
            i18n={},
            expected_revision=0,
        )
        _link, token = preview_links.create(_Staff(), keys=[HOME])

    url = reverse("builder:preview-media", kwargs={"token": token})
    on_a = TenantClient(mt_tenant).get(url, {"ids": str(asset.pk)})
    on_b = TenantClient(mt_tenant_b).get(url, {"ids": str(asset.pk)})

    assert on_a.status_code == 200, on_a.content
    assert [row["id"] for row in on_a.json()["results"]] == [asset.pk]
    assert on_b.status_code == 404
    assert on_b.json()["code"] == "preview_link_expired"
