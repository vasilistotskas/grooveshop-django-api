"""``manage.py migrate_builder_media``: image URLs become library assets,
and ``builder_rewrite_backups`` puts a run back."""

from __future__ import annotations

import contextlib
import json
import uuid
from io import StringIO
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError

from builder import media_migration, media_services, preview_links, services
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderRewriteBackup,
    BuilderVersion,
    MediaAsset,
)
from builder.storefront import clear_catalogue_cache
from tests.integration.builder.test_media import image_bytes
from tests.utils.builder import TEXT, _schema, document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
#: As the storefront states a media reference: an asset id, nothing else.
MEDIA_ID = {"type": "integer", "minimum": 1, "maximum": 2147483647}
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
    def go(*args, command="migrate_builder_media"):
        out = StringIO()
        with mock.patch(
            "django_tenants.utils.tenant_context",
            lambda _tenant: contextlib.nullcontext(),
        ):
            call_command(
                command,
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


def test_a_list_keeps_its_order_and_loses_a_slot_no_list_fills(
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

        with pytest.raises(CommandError, match="'test' is no image path"):
            run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_an_address_outside_the_store(self, admin, run):
        save_and_publish(
            admin,
            document(("a", banner(imageUrl="https://example.org/a.png"))),
        )

        with pytest.raises(CommandError, match="outside this store"):
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


class TestListsThatPairByPosition:
    """``hero_carousel`` draws ``mobileArtworks[i]`` as the phone's crop of
    ``artworks[i]``, and a list of references has no empty slot."""

    @pytest.fixture
    def carousel(self, admin, stored_image):
        urls = [stored_image(80 + n, f"slide-{n}.png") for n in range(6)]
        assets = [imported(url).pk for url in urls]

        def make(images, mobile_images):
            def named(entries):
                return [urls[e] if isinstance(e, int) else e for e in entries]

            draft = document(
                (
                    "c",
                    section(
                        "hero_carousel@1",
                        {
                            "images": named(images),
                            "mobileImages": named(mobile_images),
                        },
                    ),
                )
            )
            return save_and_publish(admin, draft)

        return make, assets

    def test_a_hole_in_the_desk_list_beside_a_phone_image_stops_the_store(
        self, carousel, run
    ):
        make, _ = carousel
        doc = make([0, "", 2], [3, 4, 5])
        draft = doc.draft

        with pytest.raises(CommandError, match=r"/images/1 is empty"):
            run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_a_hole_in_the_phone_list_beside_a_desk_image_stops_the_store(
        self, carousel, run
    ):
        make, _ = carousel
        doc = make([0, 1, 2], [3, "", 5])
        draft = doc.draft

        with pytest.raises(CommandError, match=r"/mobileImages/1 is empty"):
            run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_a_slot_empty_in_both_lists_goes_from_both(self, carousel, run):
        make, assets = carousel
        doc = make([0, "", 2], [3, "", 5])

        run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["c"][
            "settings"
        ] == {
            "artworks": [assets[0], assets[2]],
            "mobileArtworks": [assets[3], assets[5]],
        }

    def test_a_desk_slot_the_phone_list_does_not_reach_goes(
        self, carousel, run
    ):
        make, assets = carousel
        doc = make([0, "", 2], [3])

        run()

        assert BuilderDocument.objects.get(pk=doc.pk).draft["sections"]["c"][
            "settings"
        ] == {
            "artworks": [assets[0], assets[2]],
            "mobileArtworks": [assets[3]],
        }


def settings_of(doc: BuilderDocument, node: str = "a") -> dict:
    return BuilderDocument.objects.get(pk=doc.pk).draft["sections"][node][
        "settings"
    ]


class TestWhatIsReported:
    def test_a_value_names_every_document_version_block_and_setting(
        self, admin, tenant, run
    ):
        outside = "https://example.org/a.png"
        doc = save_and_publish(admin, document(("a", banner(imageUrl=outside))))
        services.save_draft(
            admin,
            key=HOME,
            draft=document(
                ("a", banner(heading="Νέο")), ("b", banner(imageUrl=outside))
            ),
            i18n={},
            expected_revision=doc.draft_revision,
        )

        with pytest.raises(CommandError) as raised:
            run()

        lines = str(raised.value).splitlines()
        schema = tenant.schema_name
        assert (
            f"{schema}: {outside!r} is an address outside this store"
            in (lines[1])
        )
        setting = "settings/imageUrl (hero_banner@2)"
        assert lines[2:] == [
            f"{schema}:     {HOME} draft /sections/b/{setting}",
            f"{schema}:     {HOME} version 1 /sections/a/{setting}",
        ]

    def test_a_file_that_is_gone(self, admin, run):
        save_and_publish(
            admin, document(("a", banner(imageUrl="/uploads/gone.png")))
        )

        with pytest.raises(
            CommandError, match=r"cannot be imported \(import_not_found\)"
        ):
            run()

    def test_an_svg(self, admin, tenant, run):
        storage = MediaAsset._meta.get_field("file").storage
        name = storage.save(
            "uploads/mark.svg",
            ContentFile(b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
        )
        try:
            save_and_publish(
                admin, document(("a", banner(imageUrl=f"/{name}")))
            )

            with pytest.raises(CommandError, match="cannot be imported"):
                run()
        finally:
            storage.delete(name)


class TestDecisions:
    """What the library cannot take is decided by a person, value by value."""

    @pytest.fixture
    def decide(self, tenant, tmp_path):
        def write(images=None, alt=None, schema=None):
            path = tmp_path / "decisions.json"
            path.write_text(
                json.dumps(
                    {
                        schema or tenant.schema_name: {
                            "images": images or {},
                            "alt": alt or {},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return ("--decisions", str(path))

        return write

    @pytest.fixture
    def outside(self, admin, stored_image):
        """An address outside the store in an older version and in the draft,
        and an image of the library to use in its place."""
        url = "https://example.org/a.png"
        asset = imported(stored_image(90))
        doc = save_and_publish(
            admin, document(("a", banner(imageUrl=url, imageAlt="Παλιό")))
        )
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, imageAlt="Βιτρίνα"))),
            i18n={},
            expected_revision=doc.draft_revision,
        )
        services.publish(admin, keys=[HOME])
        return BuilderDocument.objects.get(pk=doc.pk), url, asset

    def test_a_value_becomes_the_image_named_in_every_version(
        self, outside, decide, run
    ):
        doc, url, asset = outside

        output = run(*decide(images={url: asset.pk}))

        assert settings_of(doc) == {"image": asset.pk}
        for version in BuilderVersion.objects.filter(document=doc):
            assert version.data["sections"]["a"]["settings"] == {
                "image": asset.pk
            }
        assert alt_of(asset, "el") == "Βιτρίνα"
        assert f"decision: {url!r} is image {asset.pk} (3 use(s))" in output

    def test_a_value_becomes_no_image_and_its_alt_goes_with_it(
        self, outside, decide, run
    ):
        doc, url, asset = outside

        output = run(*decide(images={url: "empty"}))

        assert settings_of(doc) == {}
        for version in BuilderVersion.objects.filter(document=doc):
            assert version.data["sections"]["a"]["settings"] == {}
        assert alt_of(asset, "el") == ""
        assert f"decision: {url!r} is no image (3 use(s))" in output

    def test_a_value_not_decided_still_stops_the_store(
        self, outside, decide, run
    ):
        doc, _url, asset = outside
        draft = doc.draft

        with pytest.raises(CommandError, match="outside this store"):
            run(*decide(images={"https://example.org/other.png": asset.pk}))

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_an_image_the_library_does_not_hold_stops_the_store(
        self, outside, decide, run
    ):
        _doc, url, asset = outside

        with pytest.raises(CommandError, match="which is not in the library"):
            run(*decide(images={url: asset.pk + 1000}))

    def test_another_stores_decision_is_not_this_stores(
        self, outside, decide, run
    ):
        _doc, url, asset = outside

        with pytest.raises(CommandError, match="outside this store"):
            run(*decide(images={url: asset.pk}, schema="another_store"))

    def test_a_decision_nothing_used_is_said(self, outside, decide, run):
        _doc, url, asset = outside
        other = "https://example.org/typo.png"

        output = run(
            *decide(
                images={url: asset.pk, other: "empty"},
                alt={str(asset.pk): {"el": "Δεν χρειάστηκε"}},
            )
        )

        assert f"decision not used: {other!r}" in output
        assert f"decision not used: image {asset.pk} [el]" in output
        assert alt_of(asset, "el") == "Βιτρίνα"

    def test_a_dry_run_takes_the_decisions(self, outside, decide, run):
        doc, url, asset = outside
        draft = doc.draft

        output = run("--dry-run", *decide(images={url: asset.pk}))

        assert "would move 1 draft(s), 2 version(s)" in output
        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "[]",
            '{"builder_store": {"image": {}}}',
            '{"builder_store": {"images": {"/a.png": "none"}}}',
            '{"builder_store": {"images": {"/a.png": true}}}',
            '{"builder_store": {"images": []}}',
            '{"builder_store": {"alt": {"twelve": {"el": "x"}}}}',
            '{"builder_store": {"alt": {"12": {"el": 3}}}}',
        ],
    )
    def test_a_file_that_does_not_say_what_it_must_is_refused(
        self, outside, tmp_path, run, content
    ):
        doc, _url, _asset = outside
        draft = doc.draft
        path = tmp_path / "decisions.json"
        path.write_text(content, encoding="utf-8")

        with pytest.raises(CommandError, match="--decisions"):
            run("--decisions", str(path))

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft

    def test_the_alt_stated_settles_uses_that_disagree(
        self, admin, stored_image, decide, run
    ):
        asset = imported(url := stored_image(91))
        save_and_publish(
            admin,
            document(
                ("a", banner(imageUrl=url, imageAlt="Ο πάγκος")),
                ("b", banner(imageUrl=url, imageAlt="Το δέντρο")),
            ),
        )

        output = run(*decide(alt={str(asset.pk): {"el": "Το εργαστήριο"}}))

        assert alt_of(asset, "el") == "Το εργαστήριο"
        assert f"decision: image {asset.pk} [el] says 'Το εργαστήριο'" in output

    def test_an_empty_alt_stated_leaves_the_image_without_one(
        self, admin, stored_image, decide, run
    ):
        asset = imported(url := stored_image(92))
        doc = save_and_publish(
            admin,
            document(
                ("a", banner(imageUrl=url, imageAlt="Ο πάγκος")),
                ("b", banner(imageUrl=url, imageAlt="Το δέντρο")),
            ),
        )

        output = run(*decide(alt={str(asset.pk): {"el": ""}}))

        assert alt_of(asset, "el") == ""
        assert settings_of(doc) == {"image": asset.pk}
        assert f"decision: image {asset.pk} [el] stays without alt" in output

    @pytest.fixture
    def carousel(self, admin, stored_image):
        """A carousel whose middle desk image is a file that is gone."""
        urls = [stored_image(93 + n, f"pair-{n}.png") for n in range(3)]
        assets = [imported(url).pk for url in urls]
        gone = "/uploads/gone.png"

        def make(mobile_images):
            settings = {
                "images": [urls[0], gone, urls[2]],
                "mobileImages": [
                    urls[1] if entry else "" for entry in mobile_images
                ],
            }
            return save_and_publish(
                admin, document(("c", section("hero_carousel@1", settings)))
            )

        return make, assets, gone

    def test_a_list_entry_decided_empty_goes_with_its_slot(
        self, carousel, decide, run
    ):
        make, (desk, phone, last), gone = carousel
        doc = make([True, False, True])

        run(*decide(images={gone: "empty"}))

        assert settings_of(doc, "c") == {
            "artworks": [desk, last],
            "mobileArtworks": [phone, phone],
        }

    def test_a_list_entry_decided_empty_beside_an_image_stops_the_store(
        self, carousel, decide, run
    ):
        make, _assets, gone = carousel
        doc = make([True, True, True])
        draft = doc.draft

        with pytest.raises(
            CommandError,
            match=rf"{HOME} version 1: hero_carousel@1 'c': /images/1 is empty",
        ):
            run(*decide(images={gone: "empty"}))

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft


class TestADryRun:
    def test_it_meets_what_the_real_run_would_writing_the_alt(
        self, admin, stored_image, run
    ):
        """An alt the library refuses is found by the dry run too: it takes
        the same steps, in a transaction it rolls back."""
        asset = imported(url := stored_image(100))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))
        BuilderDocument.objects.filter(pk=doc.pk).update(
            draft_i18n={"xx": {"a": {"/imageAlt": "Text"}}}
        )

        with pytest.raises(CommandError, match=f"image {asset.pk}"):
            run("--dry-run")

    def test_it_leaves_no_release_no_alt_and_no_backup(
        self, admin, stored_image, run
    ):
        asset = imported(url := stored_image(101))
        doc = save_and_publish(
            admin, document(("a", banner(imageUrl=url, imageAlt="Βιτρίνα")))
        )
        version = doc.versions.get()
        releases = BuilderRelease.objects.count()

        output = run("--dry-run")

        assert "would move 1 draft(s), 1 version(s)" in output
        assert "backup run" not in output
        assert BuilderDocument.objects.get(pk=doc.pk).draft == doc.draft
        assert BuilderVersion.objects.get(pk=version.pk).data == version.data
        assert alt_of(asset, "el") == ""
        assert BuilderRelease.objects.count() == releases
        assert not BuilderRewriteBackup.objects.exists()
        assert not BuilderReference.objects.filter(kind="media").exists()


COLUMNS = (
    "data",
    "i18n",
    "block_types",
    "refs",
    "catalogue_hash",
    "content_hash",
    "size_bytes",
)


class TestTheWayBack:
    """A run keeps what it replaces, and ``builder_rewrite_backups`` puts it
    back."""

    @pytest.fixture
    def moved(self, admin, stored_image, run):
        """A store with an older version, the current one and a draft, moved:
        ``(document, {version pk: its columns before}, draft before, run)``."""
        asset = imported(url := stored_image(110))
        doc = save_and_publish(
            admin,
            document(("a", banner(imageUrl=url, imageAlt="Παλιό"))),
            {"en": {"a": {"/imageAlt": "Old"}}},
        )
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, imageAlt="Βιτρίνα"))),
            i18n={"en": {"a": {"/imageAlt": "Shop window"}}},
            expected_revision=doc.draft_revision,
        )
        services.publish(admin, keys=[HOME])
        doc = BuilderDocument.objects.get(pk=doc.pk)
        versions = {
            row["pk"]: row
            for row in BuilderVersion.objects.filter(document=doc).values(
                "pk", *COLUMNS
            )
        }
        output = run()
        run_id = output.rsplit("backup run ", 1)[1].split()[0]
        return doc, versions, asset, run_id

    def test_a_run_keeps_what_each_draft_and_version_held(self, moved):
        doc, versions, _asset, run_id = moved

        backups = BuilderRewriteBackup.objects.filter(run=run_id)
        assert backups.count() == 3
        draft = backups.get(version=None)
        assert draft.document == doc
        assert draft.original == {
            "draft": doc.draft,
            "draft_i18n": doc.draft_i18n,
            "draft_catalogue_hash": doc.draft_catalogue_hash,
        }
        for pk, before in versions.items():
            assert backups.get(version=pk).original == {
                column: before[column] for column in COLUMNS
            }

    def test_restoring_puts_every_row_back_as_it_was(self, moved, run):
        doc, versions, _asset, run_id = moved
        release = BuilderRelease.objects.order_by("-number").first()

        output = run("--restore", run_id, command="builder_rewrite_backups")

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert (after.draft, after.draft_i18n) == (doc.draft, doc.draft_i18n)
        assert after.draft_catalogue_hash == doc.draft_catalogue_hash
        assert after.draft_revision == doc.draft_revision
        for row in BuilderVersion.objects.filter(document=doc).values(
            "pk", *COLUMNS
        ):
            assert row == versions[row["pk"]]
        assert not BuilderRewriteBackup.objects.exists()
        assert "restored 1 draft(s), 2 version(s); 0 left" in output
        again = BuilderRelease.objects.order_by("-number").first()
        assert again.number == release.number + 1
        assert again.versions == release.versions
        assert not BuilderReference.objects.filter(kind="media").exists()

    def test_the_alt_written_on_the_image_stays(self, moved, run):
        _doc, _versions, asset, run_id = moved

        run("--restore", run_id, command="builder_rewrite_backups")

        assert alt_of(asset, "el") == "Βιτρίνα"
        assert alt_of(asset, "en") == "Shop window"

    def test_the_move_can_be_run_again_after_a_restore(self, moved, run):
        doc, _versions, asset, run_id = moved
        run("--restore", run_id, command="builder_rewrite_backups")

        output = run()

        assert "moved 1 draft(s), 2 version(s)" in output
        assert settings_of(doc) == {"image": asset.pk}
        runs = set(BuilderRewriteBackup.objects.values_list("run", flat=True))
        assert len(runs) == 1
        assert str(runs.pop()) != run_id

    def test_a_draft_saved_since_is_left_and_its_backup_kept(
        self, moved, admin, run
    ):
        doc, versions, asset, run_id = moved
        saved = document(("a", banner(heading="Νέα δουλειά", image=asset.pk)))
        services.save_draft(
            admin,
            key=HOME,
            draft=saved,
            i18n={},
            expected_revision=doc.draft_revision,
        )

        output = run("--restore", run_id, command="builder_rewrite_backups")

        assert BuilderDocument.objects.get(pk=doc.pk).draft == saved
        assert f"{HOME} draft: changed since the run, left as it is" in output
        assert "restored 0 draft(s), 2 version(s); 1 left" in output
        kept = BuilderRewriteBackup.objects.get()
        assert kept.version is None
        for row in BuilderVersion.objects.filter(document=doc).values(
            "pk", *COLUMNS
        ):
            assert row == versions[row["pk"]]

    def test_a_version_rewritten_since_is_left(self, moved, run):
        doc, versions, _asset, run_id = moved
        later = BuilderVersion.objects.filter(document=doc).first()
        BuilderVersion.objects.filter(pk=later.pk).update(content_hash="later")

        output = run("--restore", run_id, command="builder_rewrite_backups")

        assert f"{HOME} version {later.number}: changed since the run" in output
        assert (
            BuilderVersion.objects.get(pk=later.pk).data
            != versions[later.pk]["data"]
        )
        assert BuilderRewriteBackup.objects.get().version == later

    def test_a_dry_run_of_a_restore_changes_nothing(self, moved, run):
        doc, _versions, asset, run_id = moved
        releases = BuilderRelease.objects.count()

        output = run(
            "--restore", run_id, "--dry-run", command="builder_rewrite_backups"
        )

        assert "would restore 1 draft(s), 2 version(s)" in output
        assert settings_of(doc) == {"image": asset.pk}
        assert BuilderRewriteBackup.objects.count() == 3
        assert BuilderRelease.objects.count() == releases

    def test_the_runs_are_listed_and_one_is_deleted(self, moved, run):
        doc, _versions, asset, run_id = moved

        listed = run(command="builder_rewrite_backups")
        other = run(
            "--delete", str(uuid.uuid4()), command="builder_rewrite_backups"
        )
        deleted = run("--delete", run_id, command="builder_rewrite_backups")

        assert f"{run_id}  " in listed
        assert "3 backup(s)" in listed
        assert "deleted 0 backup(s)" in other
        assert "deleted 3 backup(s)" in deleted
        assert not BuilderRewriteBackup.objects.exists()
        assert settings_of(doc) == {"image": asset.pk}
        assert "no backups" in run(command="builder_rewrite_backups")

    def test_a_run_the_store_does_not_hold_restores_nothing(self, moved, run):
        doc, _versions, asset, _run_id = moved
        unknown = str(uuid.uuid4())

        output = run("--restore", unknown, command="builder_rewrite_backups")

        assert f"no backup of run {unknown}" in output
        assert settings_of(doc) == {"image": asset.pk}


def test_a_preview_link_keeps_the_copy_it_froze(admin, stored_image, run):
    """A share link's snapshot is outside the version history and is not
    rewritten: it opens with the URL it froze, which the storefront still
    reads, until it expires."""
    imported(url := stored_image(120))
    save_and_publish(admin, document(("a", banner(imageUrl=url))))
    _link, token = preview_links.create(admin, keys=[HOME])

    run()

    page = preview_links.open_link(token, "home", "el")
    assert page["page"]["sections"]["a"]["settings"] == {"imageUrl": url}
