"""``manage.py migrate_builder_media``: image URLs become library assets."""

from __future__ import annotations

import contextlib
from io import StringIO
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError

from builder import media_migration, media_services, services
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
    MediaAsset,
)
from builder.storefront import clear_catalogue_cache
from tests.integration.builder.test_media import image_bytes
from tests.utils.builder import TEXT, _schema, document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
MEDIA_ID = {"type": "integer"}
MEDIA_REF = {"field": "ref", "kind": "media", "onMissing": "empty"}


def banner_block() -> dict:
    return {
        "type": "hero_banner",
        "version": 2,
        "accepts": [2],
        "schema": _schema(
            heading=TEXT,
            imageUrl=TEXT,
            mobileImageUrl=TEXT,
            imageAlt=TEXT,
            image=MEDIA_ID,
            mobileImage=MEDIA_ID,
        ),
        "translatable": ["/heading", "/imageAlt"],
        "refs": {},
        "linkRefs": [],
        "data": {"/image": MEDIA_REF, "/mobileImage": MEDIA_REF},
        "limits": {},
        "children": {"allowed": []},
        "kinds": ["section"],
    }


def carousel_block() -> dict:
    urls = {"type": "array", "items": TEXT}
    ids = {"type": "array", "items": MEDIA_ID}
    return {
        "type": "hero_carousel",
        "version": 1,
        "accepts": [1],
        "schema": _schema(
            images=urls, mobileImages=urls, artworks=ids, mobileArtworks=ids
        ),
        "translatable": [],
        "refs": {},
        "linkRefs": [],
        "data": {"/artworks/*": MEDIA_REF, "/mobileArtworks/*": MEDIA_REF},
        "limits": {},
        "children": {"allowed": []},
        "kinds": ["section"],
    }


@pytest.fixture(autouse=True)
def blocks(catalogue, features_on):
    catalogue.blocks = [
        *catalogue.blocks,
        banner_block(),
        carousel_block(),
    ]
    catalogue.data_limits = {
        "maxPickedIds": 24,
        "maxFilterIds": 5,
        "maxQueryLimit": 24,
        "maxQueriesPerDocument": 12,
    }
    clear_catalogue_cache()


@pytest.fixture
def stored_image(tenant):
    """Puts a distinct image in this store's uploads, as the URL a document
    holds, and removes them afterwards."""
    storage = MediaAsset._meta.get_field("file").storage
    names: list[str] = []

    def make(width: int, name: str = "picture.png") -> str:
        saved = storage.save(
            f"uploads/{name}",
            ContentFile(image_bytes("PNG", size=(width, 20))),
        )
        names.append(saved)
        return "/" + saved

    yield make
    for name in names:
        storage.delete(name)


def imported(url: str) -> MediaAsset:
    return media_services.store_import(media_migration.import_url_of(url)).asset


def alt_of(asset: MediaAsset, locale: str) -> str:
    """The image's alt in exactly *locale*: an image with none has no row."""
    return MediaAsset.objects.get(pk=asset.pk).safe_translation_getter(
        "alt", language_code=locale, default=""
    )


def save_and_publish(admin, draft, i18n=None, key=HOME):
    services.save_draft(
        admin, key=key, draft=draft, i18n=i18n or {}, expected_revision=0
    )
    services.publish(admin, keys=[key])
    return BuilderDocument.objects.get(key=key)


def banner(**settings) -> dict:
    return section("hero_banner@2", settings)


@pytest.fixture
def run(tenant):
    def go(*args):
        out = StringIO()
        with mock.patch(
            "django_tenants.utils.tenant_context",
            lambda _tenant: contextlib.nullcontext(),
        ):
            call_command(
                "migrate_builder_media",
                "--tenant",
                tenant.schema_name,
                *args,
                stdout=out,
            )
        return out.getvalue()

    return go


class TestAMove:
    @pytest.fixture
    def stored(self, admin, stored_image):
        url = stored_image(40)
        mobile = stored_image(41)
        asset, mobile_asset = imported(url), imported(mobile)
        draft = document(
            (
                "a",
                banner(
                    heading="Καλώς ήρθατε",
                    imageUrl=url,
                    mobileImageUrl=mobile,
                    imageAlt="Βιτρίνα",
                ),
            )
        )
        i18n = {"en": {"a": {"/imageAlt": "Shop window", "/heading": "Hello"}}}
        return save_and_publish(admin, draft, i18n), asset, mobile_asset

    def test_the_draft_and_the_version_hold_the_asset_and_no_url(
        self, stored, run
    ):
        doc, asset, mobile_asset = stored
        version = doc.versions.get()

        run()

        for settings in (
            BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["a"][
                "settings"
            ],
            BuilderVersion.objects.get(pk=version.pk).data["sections"]["a"][
                "settings"
            ],
        ):
            assert settings == {
                "heading": "Καλώς ήρθατε",
                "image": asset.pk,
                "mobileImage": mobile_asset.pk,
            }

    def test_the_alt_moves_to_the_image_per_language_and_leaves_the_overlay(
        self, stored, run
    ):
        doc, asset, _ = stored

        run()

        assert alt_of(asset, "el") == "Βιτρίνα"
        assert alt_of(asset, "en") == "Shop window"
        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft_i18n == {"en": {"a": {"/heading": "Hello"}}}
        assert after.versions.get().i18n == {"en": {"a": {"/heading": "Hello"}}}

    def test_a_document_keeps_its_identity(self, stored, run):
        doc, _, _ = stored
        version = doc.versions.get()

        run()

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft_revision == doc.draft_revision
        assert after.versions.get().number == version.number

    def test_it_ends_with_a_release_of_the_same_versions_and_their_references(
        self, stored, run
    ):
        _, asset, _ = stored
        before = BuilderRelease.objects.order_by("-number").first()

        run()

        release = BuilderRelease.objects.order_by("-number").first()
        assert release.number == before.number + 1
        assert release.versions == before.versions
        assert BuilderReference.objects.filter(
            kind="media", target=str(asset.pk), document=HOME
        ).exists()

    def test_a_second_run_finds_nothing_and_releases_nothing(self, stored, run):
        run()
        releases = BuilderRelease.objects.count()

        output = run()

        assert BuilderRelease.objects.count() == releases
        assert "0 draft(s), 0 version(s)" in output

    def test_a_dry_run_writes_nothing(self, stored, run):
        doc, asset, _ = stored
        draft = doc.draft
        releases = BuilderRelease.objects.count()

        output = run("--dry-run")

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft == draft
        assert alt_of(asset, "el") == ""
        assert BuilderRelease.objects.count() == releases
        assert "would move 1 draft(s), 1 version(s)" in output


def test_a_list_keeps_its_order_and_loses_its_empty_entries(
    admin, stored_image, run
):
    first, second = stored_image(50), stored_image(51)
    one, two = imported(first), imported(second)
    draft = document(
        (
            "c",
            section(
                "hero_carousel@1",
                {"images": [second, "", first], "mobileImages": []},
            ),
        )
    )
    doc = save_and_publish(admin, draft)

    run()

    settings = BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["c"][
        "settings"
    ]
    assert settings == {"artworks": [two.pk, one.pk]}


def test_a_chosen_image_stays_and_the_url_beside_it_goes(
    admin, stored_image, run
):
    old, chosen = stored_image(52), stored_image(53)
    imported(old)
    kept = imported(chosen)
    doc = save_and_publish(
        admin,
        document(("a", banner(imageUrl=old, image=kept.pk, imageAlt="Παλιό"))),
    )

    run()

    assert BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["a"][
        "settings"
    ] == {"image": kept.pk}


def test_a_path_as_django_writes_it_is_an_image_of_the_store(
    admin, tenant, stored_image, run
):
    url = stored_image(54)
    asset = imported(url)
    written = f"media/{tenant.schema_name}{url}"
    doc = save_and_publish(admin, document(("a", banner(imageUrl=written))))

    run()

    assert (
        BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["a"][
            "settings"
        ]["image"]
        == asset.pk
    )


class TestAlt:
    @pytest.fixture
    def shared(self, admin, stored_image):
        url = stored_image(60)
        asset = imported(url)
        draft = document(
            ("a", banner(imageUrl=url, imageAlt="Ο πάγκος")),
            ("b", banner(imageUrl=url, imageAlt="Το δέντρο")),
        )
        return save_and_publish(admin, draft), asset

    def test_uses_that_disagree_stop_the_store_with_nothing_written(
        self, shared, run
    ):
        doc, asset = shared
        draft = doc.draft

        with pytest.raises(CommandError) as raised:
            run()

        assert "Ο πάγκος" in str(raised.value)
        assert "Το δέντρο" in str(raised.value)
        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft
        assert alt_of(asset, "el") == ""

    def test_writing_it_on_the_image_settles_it_and_says_who_gave_way(
        self, shared, admin, run
    ):
        doc, asset = shared
        media_services.update_asset(admin, asset, alt={"el": "Ο πάγκος"})

        output = run()

        assert alt_of(asset, "el") == "Ο πάγκος"
        assert "Το δέντρο" in output
        assert "imageUrl" not in str(
            BuilderDocument.objects.get(pk=doc.pk).draft
        )

    def test_uses_that_agree_are_one_alt(self, admin, stored_image, run):
        url = stored_image(61)
        asset = imported(url)
        draft = document(
            ("a", banner(imageUrl=url, imageAlt="Ο πάγκος")),
            ("b", banner(imageUrl=url, imageAlt="Ο πάγκος")),
            ("c", banner(imageUrl=url)),
        )
        save_and_publish(admin, draft)

        run()

        assert alt_of(asset, "el") == "Ο πάγκος"

    def test_an_older_version_decides_nothing(self, admin, stored_image, run):
        url = stored_image(62)
        asset = imported(url)
        first = save_and_publish(
            admin, document(("a", banner(imageUrl=url, imageAlt="Παλιό")))
        )
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, imageAlt="Νέο"))),
            i18n={},
            expected_revision=first.draft_revision,
        )
        services.publish(admin, keys=[HOME])

        run()

        assert alt_of(asset, "el") == "Νέο"
        for version in BuilderVersion.objects.filter(document=first):
            assert version.data["sections"]["a"]["settings"] == {
                "image": asset.pk
            }


class TestWhatStopsAStore:
    def test_a_value_that_is_no_image(self, admin, run):
        doc = save_and_publish(
            admin, document(("a", banner(imageUrl="test", imageAlt="test")))
        )
        draft = doc.draft

        with pytest.raises(CommandError, match="no image of this store"):
            run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_an_address_outside_the_store(self, admin, run):
        save_and_publish(
            admin,
            document(("a", banner(imageUrl="https://example.org/a.png"))),
        )

        with pytest.raises(CommandError, match="no image of this store"):
            run()

    def test_an_image_not_imported_yet(self, admin, stored_image, run):
        url = stored_image(70)
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))
        draft = doc.draft

        with pytest.raises(CommandError, match="run import_builder_media"):
            run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft
        assert not MediaAsset.objects.exists()

    def test_a_document_that_does_not_validate_writes_nothing_of_the_store(
        self, admin, stored_image, run
    ):
        url = stored_image(71)
        asset = imported(url)
        doc = save_and_publish(
            admin, document(("a", banner(imageUrl=url, imageAlt="Κείμενο")))
        )
        version = doc.versions.get()
        bad = document(("a", banner(imageUrl=url, nonsense=True)))
        BuilderDocument.objects.filter(pk=doc.pk).update(draft=bad)

        with pytest.raises(CommandError, match=rf"{HOME} draft"):
            run()

        after = BuilderVersion.objects.get(pk=version.pk)
        assert after.data["sections"]["a"]["settings"]["imageUrl"] == url
        assert alt_of(asset, "el") == ""

    def test_an_image_saved_while_it_runs_is_reported(
        self, admin, stored_image, run
    ):
        url = stored_image(72)
        imported(url)
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))
        version = doc.versions.get()
        late = document(("a", banner(imageUrl=stored_image(73, "late.png"))))
        read = media_migration.stored_uses

        def read_then_someone_saves():
            uses = read()
            BuilderDocument.objects.filter(pk=doc.pk).update(draft=late)
            return uses

        with (
            mock.patch.object(
                media_migration, "stored_uses", read_then_someone_saves
            ),
            pytest.raises(CommandError, match="run it again"),
        ):
            run()

        after = BuilderVersion.objects.get(pk=version.pk)
        assert after.data["sections"]["a"]["settings"]["imageUrl"] == url
        assert not BuilderRelease.objects.filter(number=2).exists()
