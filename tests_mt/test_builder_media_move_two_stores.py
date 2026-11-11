"""``migrate_builder_media`` over two real stores with one decisions file.

Each store is its own transaction, so one can be moved and committed while
the other stops. The file is then fixed and given again, as it is: what the
first store's run recorded as applied is said and stops nothing.
"""

from __future__ import annotations

import io
import json
from io import StringIO
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django_tenants.utils import tenant_context
from PIL import Image

from builder import media_services, services
from builder.management.commands.migrate_builder_media import Command
from builder.models import BuilderDocument, BuilderRewriteRun
from builder.storefront import clear_catalogue_cache
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tests.utils.builder import TEXT, FakeCatalogue, _schema, document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
OUTSIDE = "https://example.org/a.png"
MEDIA_ID = {"type": "integer", "minimum": 1}


class _Staff:
    pk = 1

    def has_perm(self, permission: str) -> bool:
        return True


setattr(_Staff, PLATFORM_IDENTITY_ATTR, True)


def banner_block() -> dict:
    return {
        "type": "hero_banner",
        "version": 2,
        "accepts": [2],
        "schema": _schema(imageUrl=dict(TEXT), image=dict(MEDIA_ID)),
        "translatable": [],
        "refs": {},
        "linkRefs": [],
        "data": {
            "/image": {"field": "ref", "kind": "media", "onMissing": "empty"}
        },
        "limits": {},
        "children": {"allowed": []},
        "kinds": ["section"],
    }


@pytest.fixture(autouse=True)
def _storefront(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    clear_catalogue_cache()
    cache.clear()
    fake = FakeCatalogue()
    fake.blocks = [*fake.blocks, banner_block()]
    fake.data_limits = {
        "maxPickedIds": 24,
        "maxFilterIds": 5,
        "maxQueryLimit": 24,
        "maxQueriesPerDocument": 12,
    }
    with (
        mock.patch("builder.storefront.requests.get", side_effect=fake.get),
        mock.patch("builder.services.feature_on", return_value=True),
    ):
        yield
    clear_catalogue_cache()


def png(shade: int) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (shade, 0, 0)).save(buffer, format="PNG")
    return SimpleUploadedFile("a.png", buffer.getvalue(), "image/png")


@pytest.fixture
def stores(mt_tenant, mt_tenant_b):
    """Two stores, each with a page that names an address outside it and an
    image of its own library to use in its place: ``{schema: asset id}``."""
    assets = {}
    for shade, tenant in enumerate((mt_tenant, mt_tenant_b)):
        with tenant_context(tenant):
            assets[tenant.schema_name] = media_services.create_asset(
                _Staff(), upload=png(shade * 40)
            ).asset.pk
            services.save_draft(
                _Staff(),
                key=HOME,
                draft=document(
                    ("a", section("hero_banner@2", {"imageUrl": OUTSIDE}))
                ),
                i18n={},
                expected_revision=0,
            )
            services.publish(_Staff(), keys=[HOME])
    return assets


def move(tmp_path, decided: dict, *scope: str) -> str:
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(decided), encoding="utf-8")
    out = StringIO()
    call_command(
        "migrate_builder_media", *scope, "--decisions", str(path), stdout=out
    )
    return out.getvalue()


def image_of(tenant) -> dict:
    with tenant_context(tenant):
        return BuilderDocument.objects.get(key=HOME).draft["sections"]["a"][
            "settings"
        ]


def test_the_file_is_given_again_after_one_store_stopped(
    stores, mt_tenant, mt_tenant_b, tmp_path
):
    first, second = mt_tenant.schema_name, mt_tenant_b.schema_name
    decided = {first: {"images": {OUTSIDE: stores[first]}}}
    both = mock.patch.object(
        Command, "get_target_tenants", return_value=[mt_tenant, mt_tenant_b]
    )

    with both, pytest.raises(CommandError, match=f"{second}: '{OUTSIDE}'"):
        move(tmp_path, decided, "--all-tenants")
    assert image_of(mt_tenant) == {"image": stores[first]}
    assert image_of(mt_tenant_b) == {"imageUrl": OUTSIDE}

    decided[second] = {"images": {OUTSIDE: stores[second]}}
    with both:
        output = move(tmp_path, decided, "--all-tenants")

    assert f"decision already applied by an earlier run: '{OUTSIDE}'" in output
    assert image_of(mt_tenant_b) == {"image": stores[second]}
    for tenant in (mt_tenant, mt_tenant_b):
        with tenant_context(tenant):
            assert BuilderRewriteRun.objects.count() == 1


def test_one_store_is_run_with_the_file_of_both(
    stores, mt_tenant, mt_tenant_b, tmp_path
):
    first, second = mt_tenant.schema_name, mt_tenant_b.schema_name
    decided = {
        schema: {"images": {OUTSIDE: stores[schema]}}
        for schema in (first, second)
    }

    output = move(tmp_path, decided, "--tenant", second)

    assert f"decisions for {first} skipped" in output
    assert image_of(mt_tenant_b) == {"image": stores[second]}
    assert image_of(mt_tenant) == {"imageUrl": OUTSIDE}
