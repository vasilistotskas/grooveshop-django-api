"""``manage.py import_builder_media``: the images documents name by URL become
library assets, found by value, de-duplicated, external ones left alone."""

from __future__ import annotations

import json
from contextlib import contextmanager
from io import StringIO
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection

from builder import media_migration
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderVersion,
    DocumentKind,
    MediaAsset,
)
from tests.integration.builder.test_media import image_bytes
from tests.integration.builder.test_media_import import (
    BASE,
    FakeResponse,
)

pytestmark = pytest.mark.django_db

NO_HOSTS = media_migration.OwnHosts()

HOME = "template:home"
ABOUT = "page:about"


@pytest.fixture(autouse=True)
def _storefront_address(settings):
    settings.NUXT_INTERNAL_BASE_URL = BASE


def home_content(**settings_):
    return {
        "sections": {
            "a": {
                "type": "hero_banner@1",
                "settings": {
                    "imageUrl": "/img/a.png",
                    "mobileImageUrl": "/img/b.png",
                    "ctaUrl": "/products/all",
                    **settings_,
                },
            },
            "c": {
                "type": "hero_carousel@1",
                "settings": {
                    "images": ["/img/a.png", "/img/c.png"],
                    "mobileImages": [],
                },
            },
        },
        "order": ["a", "c"],
    }


@pytest.fixture
def team_url(tenant):
    """An image in this store's own uploads, as ``/uploads/<name>``. Written
    through the real storage: the name it gets is returned, so a file another
    test left behind cannot collide."""
    storage = MediaAsset._meta.get_field("file").storage
    name = storage.save(
        "uploads/team.png", ContentFile(image_bytes("PNG", size=(43, 20)))
    )
    yield "/" + name
    storage.delete(name)


@pytest.fixture
def documents(tenant, team_url):
    """A draft with images, and a published page that also uses one."""
    BuilderDocument.objects.create(
        key=HOME,
        kind=DocumentKind.TEMPLATE,
        draft=home_content(avatar="https://cdn.example.org/pic.jpg"),
        draft_i18n={"el": {"/sections/a/settings/imageAlt": "Πανό"}},
        draft_revision=1,
    )
    about = BuilderDocument.objects.create(
        key=ABOUT,
        kind=DocumentKind.PAGE,
        draft={"sections": {}, "order": []},
        draft_i18n={},
        draft_revision=1,
    )
    version = BuilderVersion.objects.create(
        document=about,
        number=1,
        data={
            "sections": {
                "m": {
                    "type": "media_text@1",
                    "settings": {"imageUrl": team_url},
                }
            },
            "order": ["m"],
        },
        i18n={},
        catalogue_hash="h",
        content_hash="c" * 64,
        size_bytes=10,
    )
    BuilderRelease.objects.create(number=1, versions={ABOUT: version.pk})
    return about


@contextmanager
def files_served():
    """The storefront answers with a different image per path, and the
    store's own uploads hold one."""
    contents = {
        "/img/a.png": image_bytes("PNG", size=(40, 20)),
        "/img/b.png": image_bytes("PNG", size=(41, 20)),
        "/img/c.png": image_bytes("PNG", size=(42, 20)),
    }

    def get(url, **kwargs):
        path = url.removeprefix(BASE)
        if path in contents:
            return FakeResponse(content=contents[path])
        return FakeResponse(404)

    with mock.patch("builder.media_services.requests.get", side_effect=get):
        yield


def run(*args):
    out = StringIO()
    call_command(
        "import_builder_media",
        f"--tenant={connection.schema_name}",
        *args,
        stdout=out,
    )
    return out.getvalue()


class TestScan:
    def test_images_are_found_by_value_in_drafts_and_published_versions(
        self, documents, team_url
    ):
        plan = media_migration.scan()

        assert sorted(plan.internal) == [
            "/img/a.png",
            "/img/b.png",
            "/img/c.png",
            f"/media/{connection.schema_name}{team_url}",
        ]
        where = {(f.document, f.source, f.path) for f in plan.found}
        assert (HOME, "draft", "/sections/c/settings/images/1") in where
        assert (ABOUT, "version 1", "/sections/m/settings/imageUrl") in where
        assert [f.url for f in plan.external] == [
            "https://cdn.example.org/pic.jpg"
        ]

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ("/img/x.JPG", "static"),
            ("/uploads/x.webp", "store"),
            ("/media/s/uploads/x.png", "store"),
            ("media/s/uploads/x.png", "store"),
            ("media/other/uploads/x.png", "external"),
            ("/media/other/uploads/x.png", "external"),
            ("https://example.org/x.png", "external"),
            ("//example.org/x.png", "external"),
            ("/img/readme.txt", None),
            ("/uploads/brochure.pdf", None),
            ("/products/all", None),
            ("mailto:a@b.co", None),
            ("javascript:alert(1)//x.png", None),
            ("plain words.png", None),
        ],
    )
    def test_what_counts_as_an_image_path(self, value, kind):
        assert media_migration.classify(value, "s", NO_HOSTS) == kind

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ("https://shop.example/img/x.png", "static"),
            ("https://shop.example/uploads/x.png", "store"),
            ("HTTPS://Shop.Example/media/s/uploads/x.png", "store"),
            ("//shop.example/img/x.png", "static"),
            ("http://shop.example:8443/img/x.png", "static"),
            ("https://shop.example/media/other/uploads/x.png", "external"),
            ("https://shop.example/products/x.png", "external"),
            ("https://media.platform/media/s/uploads/x.png", "store"),
            ("https://media.platform/media/other/uploads/x.png", "external"),
            ("https://media.platform/img/x.png", "external"),
            ("https://media.platform/uploads/x.png", "external"),
            ("https://shop.example.evil.test/img/x.png", "external"),
            ("https://evil.test/shop.example/img/x.png", "external"),
            ("https://shop.example@evil.test/img/x.png", "external"),
        ],
    )
    def test_an_address_on_the_stores_own_hosts_is_its_path(self, value, kind):
        hosts = media_migration.OwnHosts(
            store=frozenset({"shop.example"}),
            platform=frozenset({"media.platform"}),
        )

        assert media_migration.classify(value, "s", hosts) == kind

    def test_an_older_unpublished_version_is_only_read_on_request(
        self, documents
    ):
        old = BuilderVersion.objects.create(
            document=documents,
            number=2,
            data={
                "sections": {
                    "m": {
                        "type": "x@1",
                        "settings": {"imageUrl": "/img/old.png"},
                    }
                },
                "order": ["m"],
            },
            i18n={},
            catalogue_hash="h",
            content_hash="d" * 64,
            size_bytes=10,
        )

        assert "/img/old.png" not in media_migration.scan().internal
        assert (
            "/img/old.png" in media_migration.scan(all_versions=True).internal
        )
        assert old.pk


class TestTheCommand:
    def test_a_dry_run_reports_and_writes_nothing(self, documents):
        with files_served():
            output = run("--dry-run")

        assert "/img/a.png: would be imported" in output
        assert (
            "    template:home [draft] /sections/a/settings/imageUrl" in output
        )
        assert "EXTERNAL (never fetched)" in output
        assert "4 image(s) of this store (4 would be imported" in output
        assert not MediaAsset.objects.exists()

    def test_a_real_run_imports_dedupes_and_maps(
        self, documents, team_url, tmp_path
    ):
        target = tmp_path / "map.json"
        with files_served():
            output = run(f"--mapping-file={target}")
            again = run("--dry-run")

        assert MediaAsset.objects.count() == 4
        mapping = json.loads(target.read_text(encoding="utf-8"))
        assert set(mapping) == {
            "/img/a.png",
            "/img/b.png",
            "/img/c.png",
            team_url,
        }
        assert mapping["/img/a.png"] == (MediaAsset.objects.get(width=40).pk)
        assert "was imported" in output
        # Running again finds them by content: nothing is imported twice.
        assert "(0 would be imported, 4 already in the library" in again

    def test_a_file_that_cannot_be_read_is_reported_and_the_rest_carry_on(
        self, documents
    ):
        def get(url, **kwargs):
            if url.endswith("/img/b.png"):
                return FakeResponse(404)
            return FakeResponse(content=image_bytes("PNG", size=(40, 20)))

        with mock.patch("builder.media_services.requests.get", side_effect=get):
            output = run("--dry-run")

        assert "/img/b.png: FAILED (import_not_found)" in output
        assert "/img/a.png: would be imported" in output

    def test_the_mapping_file_needs_a_real_run(self, documents, tmp_path):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            run("--dry-run", f"--mapping-file={tmp_path / 'm.json'}")
