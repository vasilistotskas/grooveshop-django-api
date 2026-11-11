"""``manage.py migrate_builder_media``: image URLs become library assets,
and ``builder_rewrite_backups`` puts a run back."""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builder import (
    media_migration,
    media_services,
    preview_links,
    scheduling,
    services,
)
from builder.errors import RevisionConflict
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderRewriteBackup,
    BuilderRewriteRun,
    BuilderVersion,
    MediaAsset,
)
from builder.storefront import clear_catalogue_cache
from tenant.models import TenantDomain
from tests.integration.builder.test_media import image_bytes
from tests.utils.builder import TEXT, _schema, document, section
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

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
    return media_services.store_import(
        media_migration.import_url_of(url, media_migration.own_hosts())
    ).asset


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


@pytest.fixture
def decide(tenant, tmp_path):
    """Writes a decisions file for this store and answers its argument."""

    def write(images=None, alt=None, slots=None, schema=None):
        path = tmp_path / "decisions.json"
        path.write_text(
            json.dumps(
                {
                    schema or tenant.schema_name: {
                        "images": images or {},
                        "alt": alt or {},
                        "slots": slots or {},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return ("--decisions", str(path))

    return write


def settings_of(doc: BuilderDocument, node: str = "a") -> dict:
    return BuilderDocument.objects.get(pk=doc.pk).draft["sections"][node][
        "settings"
    ]


#: ``(images, mobileImages)`` as positions of the fixture's six pictures.
DESK_HOLE = ([0, "", 2], [3, 4, 5])
PHONE_HOLE = ([0, 1, 2], [3, "", 5])


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

        return make, assets, urls

    @staticmethod
    def slot(source: str, setting: str, position: int = 1) -> str:
        return f"{HOME} {source}: /sections/c/settings/{setting}/{position}"

    @staticmethod
    def stored(doc: BuilderDocument) -> list[dict]:
        """The carousel's settings in the draft, then in each version."""
        after = BuilderDocument.objects.get(pk=doc.pk)
        return [
            after.draft["sections"]["c"]["settings"],
            *(
                version.data["sections"]["c"]["settings"]
                for version in after.versions.all()
            ),
        ]

    @pytest.mark.parametrize(
        ("lists", "setting", "beside"),
        [(DESK_HOLE, "images", 4), (PHONE_HOLE, "mobileImages", 1)],
        ids=["desk", "phone"],
    )
    def test_a_hole_beside_an_image_stops_the_store_and_shows_both_entries(
        self, carousel, tenant, run, lists, setting, beside
    ):
        make, assets, urls = carousel
        doc = make(*lists)
        before = self.stored(doc)
        other = "mobileImages" if setting == "images" else "images"

        with pytest.raises(CommandError) as raised:
            run()

        lines = str(raised.value).splitlines()[1:]
        assert lines == [
            f"{tenant.schema_name}: {self.slot(source, setting)} "
            f"(hero_carousel@1) is empty: /{setting}/1 = '', /{other}/1 = "
            f"{urls[beside]!r} (image {assets[beside]}). Decide an image for "
            'it, or "remove" to take position 1 out of every list of the block'
            for source in ("draft", "version 1")
        ]
        assert self.stored(doc) == before

    @pytest.mark.parametrize(
        ("lists", "setting", "filled"),
        [
            (DESK_HOLE, "images", ([0, 1, 2], [3, 4, 5])),
            (PHONE_HOLE, "mobileImages", ([0, 1, 2], [3, 4, 5])),
        ],
        ids=["desk", "phone"],
    )
    def test_an_image_decided_for_a_hole_fills_it_in_draft_and_version(
        self, carousel, decide, run, lists, setting, filled
    ):
        make, assets, _urls = carousel
        doc = make(*lists)
        image = assets[1] if setting == "images" else assets[4]

        output = run(
            *decide(
                slots={
                    self.slot("draft", setting): image,
                    self.slot("version 1", setting): image,
                }
            )
        )

        assert (
            self.stored(doc)
            == [
                {
                    "artworks": [assets[n] for n in filled[0]],
                    "mobileArtworks": [assets[n] for n in filled[1]],
                }
            ]
            * 2
        )
        assert (
            f"decision: {self.slot('version 1', setting)} is image {image}"
            in output
        )

    @pytest.mark.parametrize(
        ("lists", "setting"),
        [(DESK_HOLE, "images"), (PHONE_HOLE, "mobileImages")],
        ids=["desk", "phone"],
    )
    def test_a_position_decided_removed_goes_from_both_lists(
        self, carousel, decide, run, lists, setting
    ):
        make, assets, _urls = carousel
        doc = make(*lists)

        output = run(
            *decide(
                slots={
                    self.slot("draft", setting): "remove",
                    self.slot("version 1", setting): "remove",
                }
            )
        )

        assert (
            self.stored(doc)
            == [
                {
                    "artworks": [assets[0], assets[2]],
                    "mobileArtworks": [assets[3], assets[5]],
                }
            ]
            * 2
        )
        assert (
            f"decision: {self.slot('draft', setting)} is removed with its "
            "position"
        ) in output

    def test_each_document_is_decided_for_itself(self, carousel, decide, run):
        make, _assets, _urls = carousel
        doc = make(*DESK_HOLE)
        before = self.stored(doc)

        with pytest.raises(CommandError) as raised:
            run(*decide(slots={self.slot("draft", "images"): "remove"}))

        assert self.slot("version 1", "images") in str(raised.value)
        assert self.slot("draft", "images") not in str(raised.value)
        assert self.stored(doc) == before

    def test_an_image_the_library_does_not_hold_stops_the_store(
        self, carousel, decide, run
    ):
        make, assets, _urls = carousel
        doc = make(*DESK_HOLE)
        before = self.stored(doc)

        with pytest.raises(CommandError, match="which is not in the library"):
            run(
                *decide(
                    slots={
                        self.slot("draft", "images"): max(assets) + 1000,
                        self.slot("version 1", "images"): "remove",
                    }
                )
            )

        assert self.stored(doc) == before

    def test_a_decision_for_a_slot_that_is_no_hole_stops_the_store(
        self, carousel, decide, run
    ):
        make, assets, _urls = carousel
        doc = make([0, 1], [3, 4])
        before = self.stored(doc)
        unused = self.slot("draft", "images", 0)

        with pytest.raises(
            CommandError, match=f"decision not used: {re.escape(unused)}"
        ):
            run(*decide(slots={unused: assets[5]}))

        assert self.stored(doc) == before

    def test_a_decided_run_is_put_back_as_it_was(self, carousel, decide, run):
        make, _assets, _urls = carousel
        doc = make(*PHONE_HOLE)
        before = self.stored(doc)
        output = run(
            *decide(
                slots={
                    self.slot("draft", "mobileImages"): "remove",
                    self.slot("version 1", "mobileImages"): "remove",
                }
            )
        )
        run_id = output.rsplit("backup run ", 1)[1].split()[0]
        assert self.stored(doc) != before

        run("--restore", run_id, command="builder_rewrite_backups")

        assert self.stored(doc) == before

    def test_a_slot_empty_in_both_lists_goes_from_both(self, carousel, run):
        make, assets, _urls = carousel
        doc = make([0, "", 2], [3, "", 5])

        run()

        assert settings_of(doc, "c") == {
            "artworks": [assets[0], assets[2]],
            "mobileArtworks": [assets[3], assets[5]],
        }

    def test_a_desk_slot_the_phone_list_does_not_reach_goes(
        self, carousel, run
    ):
        make, assets, _urls = carousel
        doc = make([0, "", 2], [3])

        run()

        assert settings_of(doc, "c") == {
            "artworks": [assets[0], assets[2]],
            "mobileArtworks": [assets[3]],
        }

    def test_a_removed_position_leaves_a_chosen_list_too(
        self, admin, carousel, decide, run
    ):
        """One list already holds references, the other still URLs: the
        position goes from both, whichever shape each has."""
        _make, assets, urls = carousel
        doc = save_and_publish(
            admin,
            document(
                (
                    "c",
                    section(
                        "hero_carousel@1",
                        {
                            "artworks": assets[:3],
                            "mobileImages": [urls[3], "", urls[5]],
                        },
                    ),
                )
            ),
        )

        run(
            *decide(
                slots={
                    self.slot("draft", "mobileImages"): "remove",
                    self.slot("version 1", "mobileImages"): "remove",
                }
            )
        )

        assert settings_of(doc, "c") == {
            "artworks": [assets[0], assets[2]],
            "mobileArtworks": [assets[3], assets[5]],
        }


class TestTheStoresOwnHosts:
    """An absolute address on one of the store's own hosts names the store's
    own file, by its path: it is found in the library like the bare path, and
    is never fetched."""

    @pytest.fixture
    def hosts(self, tenant, settings):
        TenantDomain.objects.create(
            tenant=tenant, domain="shop.example", is_primary=True
        )
        TenantDomain.objects.create(
            tenant=tenant, domain="assets.shop.example", is_primary=False
        )
        settings.MEDIA_STREAM_BASE_URL = "https://assets.platform.example"
        settings.STATIC_BASE_URL = "https://static.platform.example"

    def test_they_are_the_tenants_domains_and_the_platforms_media_hosts(
        self, hosts
    ):
        own = media_migration.own_hosts()

        assert own.store == {"shop.example", "assets.shop.example"}
        assert own.platform == {
            "assets.platform.example",
            "static.platform.example",
        }

    @pytest.mark.parametrize(
        "address",
        [
            "https://shop.example{path}",
            "https://assets.shop.example/media/{schema}{path}",
            "//SHOP.example{path}",
            "https://assets.platform.example/media/{schema}{path}",
            "https://static.platform.example/media/{schema}{path}",
        ],
    )
    def test_an_address_on_one_of_them_becomes_the_stores_image(
        self, hosts, admin, tenant, stored_image, run, address
    ):
        path = stored_image(130)
        asset = imported(path)
        url = address.format(path=path, schema=tenant.schema_name)
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))

        run()

        assert settings_of(doc) == {"image": asset.pk}

    @pytest.mark.parametrize(
        "address",
        [
            "https://elsewhere.example{path}",
            "https://assets.platform.example{path}",
            "https://assets.platform.example/media/another_store{path}",
        ],
    )
    def test_an_address_that_is_not_the_stores_still_stops_it(
        self, hosts, admin, stored_image, run, address
    ):
        path = stored_image(131)
        imported(path)
        url = address.format(path=path)
        save_and_publish(admin, document(("a", banner(imageUrl=url))))

        with pytest.raises(CommandError, match="outside this store"):
            run()

    def test_the_import_takes_it_too(self, hosts, admin, stored_image):
        path = stored_image(132)
        url = f"https://shop.example{path}"
        save_and_publish(admin, document(("a", banner(imageUrl=url))))

        plan = media_migration.scan()
        outcomes = media_migration.run(plan, dry_run=False)

        asset = MediaAsset.objects.get()
        assert not plan.external
        assert media_migration.mapping(plan, outcomes) == {url: asset.pk}


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
            f"{schema}:     {HOME} draft: /sections/b/{setting}",
            f"{schema}:     {HOME} version 1: /sections/a/{setting}",
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
            '{"builder_store": {"slots": {"page:a draft: /x/0": "empty"}}}',
            '{"builder_store": {"slots": []}}',
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
            match=(
                rf"{HOME} version 1: /sections/c/settings/images/1 "
                r"\(hero_carousel@1\) is empty: /images/1 = "
                r"'/uploads/gone.png' \(no image\)"
            ),
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

    @pytest.fixture
    def asset_settings(self, moved):
        _doc, _versions, asset, _run_id = moved
        return {"image": asset.pk}

    def test_a_run_keeps_what_each_draft_and_version_held(self, moved):
        doc, versions, _asset, run_id = moved

        backups = BuilderRewriteBackup.objects.filter(run__run=run_id)
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
        for row in BuilderVersion.objects.filter(document=doc).values(
            "pk", *COLUMNS
        ):
            assert row == versions[row["pk"]]
        assert not BuilderRewriteBackup.objects.exists()
        assert not BuilderRewriteRun.objects.exists()
        assert "restored 1 draft(s), 2 version(s)" in output
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
        kept = BuilderRewriteRun.objects.get()
        assert str(kept.run) != run_id
        assert kept.backups.count() == 3

    @pytest.fixture
    def saved_since(self, moved, admin):
        """The run's store with its draft saved by someone afterwards."""
        doc, versions, asset, run_id = moved
        saved = document(("a", banner(heading="Νέα δουλειά", image=asset.pk)))
        services.save_draft(
            admin,
            key=HOME,
            draft=saved,
            i18n={},
            expected_revision=BuilderDocument.objects.get(
                pk=doc.pk
            ).draft_revision,
        )
        return doc, versions, saved, run_id

    def test_one_row_changed_since_and_nothing_is_restored(
        self, saved_since, asset_settings, run
    ):
        doc, _versions, saved, run_id = saved_since
        releases = BuilderRelease.objects.count()

        with pytest.raises(
            CommandError,
            match=f"{HOME} draft: saved since the run; nothing of this store",
        ):
            run("--restore", run_id, command="builder_rewrite_backups")

        assert BuilderDocument.objects.get(pk=doc.pk).draft == saved
        for version in BuilderVersion.objects.filter(document=doc):
            assert version.data["sections"]["a"]["settings"] == asset_settings
        assert BuilderRewriteBackup.objects.count() == 3
        assert BuilderRelease.objects.count() == releases

    def test_the_rest_is_restored_only_when_asked_and_still_fails(
        self, saved_since, run
    ):
        doc, versions, saved, run_id = saved_since
        releases = BuilderRelease.objects.count()

        with pytest.raises(
            CommandError,
            match=f"{HOME} draft: saved since the run; left as it is",
        ):
            run(
                "--restore",
                run_id,
                "--leave-changed-rows",
                command="builder_rewrite_backups",
            )

        assert BuilderDocument.objects.get(pk=doc.pk).draft == saved
        for row in BuilderVersion.objects.filter(document=doc).values(
            "pk", *COLUMNS
        ):
            assert row == versions[row["pk"]]
        assert BuilderRewriteBackup.objects.get().version is None
        assert BuilderRelease.objects.count() == releases + 1

    def test_a_version_rewritten_since_stops_the_restore_too(self, moved, run):
        doc, _versions, _asset, run_id = moved
        later = BuilderVersion.objects.filter(document=doc).first()
        BuilderVersion.objects.filter(pk=later.pk).update(content_hash="later")
        draft = BuilderDocument.objects.get(pk=doc.pk).draft

        with pytest.raises(
            CommandError,
            match=f"{HOME} version {later.number}: rewritten since the run",
        ):
            run("--restore", run_id, command="builder_rewrite_backups")

        assert BuilderDocument.objects.get(pk=doc.pk).draft == draft
        assert BuilderRewriteBackup.objects.count() == 3

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

    def test_the_runs_of_a_store_are_listed(self, moved, run):
        _doc, _versions, _asset, run_id = moved

        listed = run(command="builder_rewrite_backups")

        assert f"{run_id}  " in listed
        assert "migrate_builder_media  3 backup(s)" in listed

    def test_a_run_no_store_holds_restores_nothing_and_fails(self, moved, run):
        doc, _versions, asset, _run_id = moved
        unknown = str(uuid.uuid4())

        with pytest.raises(CommandError, match=f"holds run {unknown}"):
            run("--restore", unknown, command="builder_rewrite_backups")

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


def lock_order(queries) -> list[str]:
    """Which lock each locking statement of *queries* took, in order."""
    order = []
    for query in queries:
        sql = query["sql"]
        if "pg_advisory_xact_lock" in sql:
            order.append("release sequence")
        elif "FOR UPDATE" in sql and '"builder_builderdocument"' in sql:
            order.append("documents")
    return order


class TestWhatARunMeetsInAStore:
    """A store in use: a deleted page, staff publishing, an editor open."""

    @pytest.fixture
    def stored(self, admin, stored_image):
        asset = imported(url := stored_image(140))
        doc = save_and_publish(
            admin, document(("a", banner(imageUrl=url, imageAlt="Βιτρίνα")))
        )
        return doc, url, asset

    def test_a_deleted_pages_draft_is_moved_with_its_versions(
        self, admin, stored_image, run
    ):
        asset = imported(url := stored_image(141))
        page = save_and_publish(
            admin, document(("a", banner(imageUrl=url))), key="page:about"
        )
        services.delete_page(
            admin, key="page:about", expected_revision=page.draft_revision
        )

        run()

        deleted = BuilderDocument.objects.deleted_only().get(pk=page.pk)
        assert deleted.draft["sections"]["a"]["settings"] == {"image": asset.pk}
        assert deleted.versions.get().data["sections"]["a"]["settings"] == {
            "image": asset.pk
        }

    def test_the_release_sequence_is_locked_before_any_document(
        self, stored, run
    ):
        """The order of every writer of a release (``services.publish``): a
        run that locked documents first could deadlock with a publish. Read
        from the statements a run sends, with no second connection."""
        with CaptureQueriesContext(connection) as moved:
            output = run()
        run_id = output.rsplit("backup run ", 1)[1].split()[0]
        with CaptureQueriesContext(connection) as restored:
            run("--restore", run_id, command="builder_rewrite_backups")

        for queries in (moved, restored):
            order = lock_order(queries.captured_queries)
            assert order[0] == "release sequence"
            assert "documents" in order

    def test_the_draft_moves_to_a_new_revision_and_stays_published(
        self, stored, run
    ):
        doc, _url, _asset = stored
        version = doc.versions.get()

        run()

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft_revision == doc.draft_revision + 1
        assert after.published_revision == after.draft_revision
        assert after.versions.get().number == version.number

    def test_an_editor_open_since_before_gets_the_conflict(
        self, stored, admin, run
    ):
        """The copy an editor holds still has the URL. Its save names the
        revision it loaded, which the run has moved on."""
        doc, _url, asset = stored

        run()

        with pytest.raises(RevisionConflict):
            services.save_draft(
                admin,
                key=HOME,
                draft=doc.draft,
                i18n=doc.draft_i18n,
                expected_revision=doc.draft_revision,
            )
        assert settings_of(doc) == {"image": asset.pk}

    def test_a_draft_with_unpublished_changes_still_has_them(
        self, stored, admin, run
    ):
        doc, url, _asset = stored
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, heading="Νέο"))),
            i18n={},
            expected_revision=doc.draft_revision,
        )
        before = BuilderDocument.objects.get(pk=doc.pk)

        run()

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft_revision == before.draft_revision + 1
        assert after.published_revision == before.published_revision

    def test_a_waiting_publish_follows_the_draft_it_froze(
        self, stored, admin, run
    ):
        doc, url, _asset = stored
        saved = services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, heading="Αύριο"))),
            i18n={},
            expected_revision=doc.draft_revision,
        )
        schedule = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(days=1)
        )
        assert schedule.revisions == {HOME: saved.draft_revision}

        run()

        schedule.refresh_from_db()
        assert schedule.revisions == {HOME: saved.draft_revision + 1}

    def test_a_restore_moves_the_revision_on_again(self, stored, admin, run):
        doc, _url, _asset = stored
        output = run()
        run_id = output.rsplit("backup run ", 1)[1].split()[0]
        moved = BuilderDocument.objects.get(pk=doc.pk)

        run("--restore", run_id, command="builder_rewrite_backups")

        after = BuilderDocument.objects.get(pk=doc.pk)
        assert after.draft == doc.draft
        assert after.draft_revision == moved.draft_revision + 1
        assert after.published_revision == after.draft_revision
        with pytest.raises(RevisionConflict):
            services.save_draft(
                admin,
                key=HOME,
                draft=moved.draft,
                i18n=moved.draft_i18n,
                expected_revision=moved.draft_revision,
            )

    def test_a_version_deleted_without_django_takes_no_backup_down(
        self, stored, admin, run
    ):
        """A release from before this table deletes versions (checkpoint
        pruning) knowing nothing of the backups. No constraint of the
        database stands in its way, and a restore puts back the rest."""
        doc, url, _asset = stored
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, heading="Νέο"))),
            i18n={},
            expected_revision=doc.draft_revision,
        )
        old = services.create_checkpoint(admin, key=HOME)
        before = BuilderDocument.objects.get(pk=doc.pk).draft
        output = run()
        run_id = output.rsplit("backup run ", 1)[1].split()[0]

        BuilderVersion.objects.filter(pk=old.pk)._raw_delete(connection.alias)
        connection.check_constraints()
        output = run("--restore", run_id, command="builder_rewrite_backups")

        assert "restored 1 draft(s), 1 version(s)" in output
        assert BuilderDocument.objects.get(pk=doc.pk).draft == before
        assert not BuilderRewriteBackup.objects.exists()


class TestTheDecisionsFileIsHeldToItsWord:
    def test_a_store_the_run_does_not_reach_is_refused(
        self, admin, stored_image, decide, run
    ):
        imported(url := stored_image(150))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))

        with pytest.raises(CommandError, match="'builder_stor'"):
            run(*decide(schema="builder_stor"))

        assert settings_of(doc) == {"imageUrl": url}

    @pytest.mark.parametrize(
        "decided",
        [
            {"images": {"https://example.org/typo.png": "empty"}},
            {"alt": {"1": {"el": "Δεν χρειάστηκε"}}},
            {
                "slots": {
                    f"{HOME} draft: /sections/a/settings/images/0": "remove"
                }
            },
        ],
        ids=["value", "alt", "slot"],
    )
    def test_a_decision_nothing_used_stops_the_store(
        self, admin, stored_image, decide, run, decided
    ):
        imported(url := stored_image(151))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))

        with pytest.raises(CommandError, match="decision not used: "):
            run(*decide(**decided))

        assert settings_of(doc) == {"imageUrl": url}

    def test_what_was_decided_is_kept_with_the_run(
        self, admin, tenant, stored_image, decide, run
    ):
        asset = imported(stored_image(152))
        url = "https://example.org/a.png"
        save_and_publish(admin, document(("a", banner(imageUrl=url))))

        output = run(*decide(images={url: asset.pk}))
        run_id = output.rsplit("backup run ", 1)[1].split()[0]
        listed = run(command="builder_rewrite_backups")

        kept = BuilderRewriteRun.objects.get(run=run_id)
        assert kept.command == "migrate_builder_media"
        assert kept.decisions == {"images": {url: asset.pk}}
        assert f'"{url}": {asset.pk}' in listed
        assert "migrate_builder_media" in listed

    def test_a_dry_run_keeps_no_run(self, admin, stored_image, run):
        imported(url := stored_image(153))
        save_and_publish(admin, document(("a", banner(imageUrl=url))))

        run("--dry-run")

        assert not BuilderRewriteRun.objects.exists()


class TestDeletingBackups:
    @pytest.fixture
    def two_runs(self, admin, stored_image, run):
        """A store with an older run and the latest one: their ids."""
        imported(url := stored_image(160))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))
        first = run().rsplit("backup run ", 1)[1].split()[0]
        moved = BuilderDocument.objects.get(pk=doc.pk)
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", banner(imageUrl=url, heading="Ξανά"))),
            i18n={},
            expected_revision=moved.draft_revision,
        )
        second = run().rsplit("backup run ", 1)[1].split()[0]
        return first, second

    def delete(self, run, *args):
        return run("--delete", *args, command="builder_rewrite_backups")

    def test_it_says_what_it_would_delete_and_waits_for_yes(
        self, two_runs, run
    ):
        first, _second = two_runs

        with pytest.raises(CommandError, match="--yes") as raised:
            self.delete(run, first)

        assert "2 backup(s)" in str(raised.value)
        assert BuilderRewriteRun.objects.filter(run=first).exists()

    def test_yes_deletes_the_run_and_its_backups(self, two_runs, run):
        first, second = two_runs

        output = self.delete(run, first, "--yes")

        assert "deleted 2 backup(s)" in output
        assert [
            str(kept)
            for kept in BuilderRewriteRun.objects.values_list("run", flat=True)
        ] == [second]
        assert not BuilderRewriteBackup.objects.filter(run__run=first).exists()

    def test_the_latest_run_of_a_store_is_kept_unless_asked_for(
        self, two_runs, run
    ):
        _first, second = two_runs

        with pytest.raises(CommandError, match="--latest-too"):
            self.delete(run, second, "--yes")
        assert BuilderRewriteRun.objects.filter(run=second).exists()

        self.delete(run, second, "--yes", "--latest-too")

        assert not BuilderRewriteRun.objects.filter(run=second).exists()

    def test_every_store_at_once_has_to_be_said(self, two_runs, tenant):
        first, _second = two_runs

        with pytest.raises(CommandError, match="--in-every-store"):
            call_command(
                "builder_rewrite_backups",
                "--all-tenants",
                "--delete",
                first,
                "--yes",
                stdout=StringIO(),
            )

        assert BuilderRewriteRun.objects.filter(run=first).exists()

    def test_a_run_no_store_holds_deletes_nothing_and_fails(
        self, two_runs, run
    ):
        unknown = str(uuid.uuid4())

        with pytest.raises(CommandError, match=f"holds run {unknown}"):
            self.delete(run, unknown, "--yes")

        assert BuilderRewriteRun.objects.count() == 2


class TestTheSameFileAgain:
    """A file whose decisions an earlier run applied to this store is not a
    stale file: the run recorded them, and a rerun says so and goes on."""

    @pytest.fixture
    def applied(self, admin, tenant, stored_image, tmp_path, run):
        """A store moved by a run that used a decision of each kind: the
        file's content, to write again, and the run's output."""
        shared, desk, phone = (
            stored_image(170 + n, f"s-{n}.png") for n in range(3)
        )
        asset, _desk, _phone = (imported(url) for url in (shared, desk, phone))
        outside = "https://example.org/a.png"
        save_and_publish(
            admin,
            document(
                ("a", banner(imageUrl=outside)),
                ("b", banner(imageUrl=shared, imageAlt="Ο πάγκος")),
                ("c", banner(imageUrl=shared, imageAlt="Το δέντρο")),
                (
                    "d",
                    section(
                        "hero_carousel@1",
                        {"images": [desk, desk], "mobileImages": ["", phone]},
                    ),
                ),
            ),
        )
        hole = "/sections/d/settings/mobileImages/0"
        stated = {
            "images": {outside: asset.pk},
            "alt": {str(asset.pk): {"el": "Το εργαστήριο"}},
            "slots": {
                f"{HOME} draft: {hole}": "remove",
                f"{HOME} version 1: {hole}": "remove",
            },
        }

        def write(**changed):
            path = tmp_path / "decisions.json"
            path.write_text(
                json.dumps(
                    {tenant.schema_name: {**stated, **changed}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return ("--decisions", str(path))

        return write, stated, run(*write())

    def test_what_an_earlier_run_applied_is_said_and_stops_nothing(
        self, applied, run
    ):
        write, stated, first = applied
        releases = BuilderRelease.objects.count()

        output = run(*write())

        assert "moved 1 draft(s), 1 version(s)" in first
        assert "moved 0 draft(s), 0 version(s)" in output
        assert output.count("decision already applied by an earlier run: ") == 3
        assert "decision already in place: image" in output
        assert "decision not used" not in output
        assert BuilderRewriteRun.objects.count() == 1
        assert BuilderRelease.objects.count() == releases
        assert BuilderRewriteRun.objects.get().decisions == stated

    def test_a_decision_no_run_applied_still_stops_the_store(
        self, applied, run
    ):
        write, stated, _first = applied
        typo = "https://example.org/typo.png"

        with pytest.raises(CommandError) as raised:
            run(*write(images={**stated["images"], typo: "empty"}))

        assert f"decision not used: {typo!r} is in no document" in str(
            raised.value
        )
        assert "example.org/a.png" not in str(raised.value)

    def test_a_decision_changed_since_it_was_applied_stops_the_store(
        self, applied, run
    ):
        write, _stated, _first = applied
        outside = "https://example.org/a.png"

        with pytest.raises(
            CommandError, match=f"decision not used: {outside!r}"
        ):
            run(*write(images={outside: "empty"}))

    def test_a_restored_run_applied_nothing_any_more(self, applied, run):
        write, _stated, first = applied
        run_id = first.rsplit("backup run ", 1)[1].split()[0]
        run("--restore", run_id, command="builder_rewrite_backups")

        output = run(*write())

        assert "moved 1 draft(s), 1 version(s)" in output
        assert "decision already applied" not in output


class TestAFileForSeveralStores:
    @pytest.fixture
    def another_store(self, tenant):
        """A second store, which the run of this one does not reach."""
        bound = bind_store_tenant(None)
        store_tenant("another_store")
        unbind_store_tenant(bound)

    def file(self, tmp_path, **stores):
        path = tmp_path / "decisions.json"
        path.write_text(json.dumps(stores), encoding="utf-8")
        return ("--decisions", str(path))

    def test_a_store_this_run_does_not_reach_is_skipped_and_said(
        self, admin, tenant, another_store, stored_image, tmp_path, run
    ):
        asset = imported(stored_image(180))
        url = "https://example.org/a.png"
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))

        output = run(
            *self.file(
                tmp_path,
                another_store={"images": {"/img/theirs.png": "empty"}},
                **{tenant.schema_name: {"images": {url: asset.pk}}},
            )
        )

        assert "decisions for another_store skipped" in output
        assert settings_of(doc) == {"image": asset.pk}

    def test_a_name_that_is_no_store_stops_the_command(
        self, admin, tenant, another_store, stored_image, tmp_path, run
    ):
        imported(url := stored_image(181))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))

        with pytest.raises(CommandError, match="'another_stor' is no store"):
            run(*self.file(tmp_path, another_stor={}))

        assert settings_of(doc) == {"imageUrl": url}


class TestADeletedPage:
    """Nobody can open a deleted page, so what it holds is settled in the
    decisions file or not at all."""

    @pytest.fixture
    def deleted(self, admin):
        def make(**settings):
            page = save_and_publish(
                admin, document(("a", banner(**settings))), key="page:about"
            )
            services.delete_page(
                admin, key="page:about", expected_revision=page.draft_revision
            )
            return page

        return make

    def test_its_lines_say_so_and_what_can_be_done(self, deleted, run):
        deleted(imageUrl="https://example.org/a.png")

        with pytest.raises(CommandError) as raised:
            run()

        message = str(raised.value)
        setting = "/sections/a/settings/imageUrl (hero_banner@2)"
        assert f"page:about draft (deleted page): {setting}" in message
        assert f"page:about version 1 (deleted page): {setting}" in message
        assert "A deleted page cannot be opened in the editor" in message

    def test_a_decision_settles_it(
        self, deleted, admin, stored_image, decide, run
    ):
        asset = imported(stored_image(190))
        page = deleted(imageUrl="https://example.org/a.png")

        run(*decide(images={"https://example.org/a.png": asset.pk}))

        after = BuilderDocument.objects.deleted_only().get(pk=page.pk)
        assert after.draft["sections"]["a"]["settings"] == {"image": asset.pk}

    def test_a_draft_no_decision_can_settle_is_left_and_stops_nothing(
        self, deleted, stored_image, run
    ):
        """It does not validate any more, which no decision changes, and it
        is never read again: a page brought back starts empty."""
        asset = imported(url := stored_image(191))
        page = deleted(imageUrl=url)
        stale = document(("a", banner(imageUrl=url, nonsense=True)))
        BuilderDocument.objects.all_with_deleted().filter(pk=page.pk).update(
            draft=stale
        )

        output = run()

        after = BuilderDocument.objects.deleted_only().get(pk=page.pk)
        assert after.draft == stale
        assert after.versions.get().data["sections"]["a"]["settings"] == {
            "image": asset.pk
        }
        assert "page:about draft (deleted page): left as it is" in output
        assert "moved 0 draft(s), 1 version(s)" in output


class TestABackupWhoseRowIsGone:
    """A version or a page deleted by code that does not know the backups
    (no constraint of the database ties them): the run is still listed."""

    @pytest.fixture
    def moved(self, admin, stored_image, run):
        imported(url := stored_image(195))
        doc = save_and_publish(admin, document(("a", banner(imageUrl=url))))
        run_id = run().rsplit("backup run ", 1)[1].split()[0]
        return doc, run_id

    def test_a_version_gone(self, moved, run):
        doc, run_id = moved
        doc.versions.all()._raw_delete(connection.alias)

        listed = run(command="builder_rewrite_backups")

        assert f"{run_id}  " in listed
        assert "2 backup(s)" in listed

    def test_a_page_gone(self, moved, run):
        doc, run_id = moved
        doc.versions.all()._raw_delete(connection.alias)
        BuilderDocument.objects.filter(pk=doc.pk)._raw_delete(connection.alias)

        listed = run(command="builder_rewrite_backups")
        restored = run("--restore", run_id, command="builder_rewrite_backups")

        assert "2 backup(s)" in listed
        assert "2 backup(s) of rows that no longer exist removed" in restored
        assert not BuilderRewriteRun.objects.exists()
