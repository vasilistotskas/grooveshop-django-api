"""Importing a store's page_config layouts and menus as builder documents.

The conversion itself is the storefront's (``builder.convert``); here it is
a stand-in that returns simple documents, so what is tested is everything
Django owns: what is sent, what is validated, what is written, and that it
is all or nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from unittest import mock

import pytest
import requests

from builder import convert, importing, legacy
from builder.convert import ConversionRefused, Converted
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
)
from core.storefront import StorefrontUnavailable
from page_config.models import (
    ContentPage,
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    PageLayout,
    PageSection,
)

pytestmark = pytest.mark.django_db

UUID_1 = "00000000-0000-4000-8000-000000000001"


def make_layout(page_type="home", *, published=True, sections=1) -> PageLayout:
    layout = PageLayout.objects.create(
        page_type=page_type, title=page_type.title(), is_published=published
    )
    for n in range(sections):
        PageSection.objects.create(
            layout=layout,
            component_type="spacer",
            title=f"Band {n}",
            props={"height_px": 10 + n},
            i18n={"en": {"title": f"English {n}"}},
        )
    return layout


def fake_convert(body: dict[str, Any]) -> Converted:
    """What the storefront would answer, minimally: one banner per layout
    section, one announcement per menu."""
    legacy_data = body["legacy"]
    if body["kind"] == "group":
        links = legacy_data.get("links") or legacy_data["columns"]
        text = (links[0].get("labels") or {}).get("el", "x") or "x"
        return Converted(
            document={
                "sections": {
                    "menu": {
                        "type": "announcement@1",
                        "settings": {"text": text},
                    }
                },
                "order": ["menu"],
            },
            i18n={},
            warnings=[{"code": "note", "path": "", "message": "fake"}],
        )
    sections = {
        s["id"]: {"type": "banner@1", "settings": {"text": s["title"] or "x"}}
        for s in legacy_data["sections"]
    }
    document: dict[str, Any] = {"sections": sections, "order": list(sections)}
    seo = legacy_data["seo"].get(body.get("defaultLocale", "el"))
    if seo:
        document["settings"] = seo
    return Converted(document=document, i18n={})


@pytest.fixture(autouse=True)
def _ready(catalogue, features_on, tenant):
    return None


@pytest.fixture
def converter():
    with mock.patch(
        "builder.importing.convert_legacy", side_effect=fake_convert
    ) as fake:
        yield fake


class TestWhatIsSent:
    def test_a_layout_is_described_with_its_sections_in_order_and_its_seo(self):
        layout = make_layout(sections=2)
        layout.create_translation("el", seo_title="Αρχική", seo_description="")
        layout.create_translation("en", seo_title="Home", seo_keywords="shop")

        sent = legacy.export_layout(layout)

        assert sent["pageType"] == "home"
        assert sent["seo"] == {
            "el": {"seoTitle": "Αρχική"},
            "en": {"seoTitle": "Home", "seoKeywords": "shop"},
        }
        assert [s["title"] for s in sent["sections"]] == ["Band 0", "Band 1"]
        first = sent["sections"][0]
        assert first["type"] == "spacer"
        assert first["isVisible"] is True
        assert first["props"] == {"height_px": 10}
        assert first["i18n"] == {"en": {"title": "English 0"}}
        assert first["id"] == str(layout.sections.first().uuid)

    def test_a_header_menu_sends_each_links_effective_label_per_language(self):
        page = ContentPage.objects.create(slug="faq", is_published=True)
        page.create_translation("el", title="Συχνές ερωτήσεις")
        page.create_translation("en", title="FAQ")
        menu = NavigationMenu.objects.create(slot="header")
        by_page = NavigationLink.objects.create(menu=menu, content_page=page)
        by_route = NavigationLink.objects.create(menu=menu, route="/products")
        by_route.create_translation("el", label="Προϊόντα")

        sent = legacy.export_menu(menu, ["el", "en"])

        assert sent["slot"] == "header"
        first, second = sent["links"]
        assert first == {
            "id": str(by_page.uuid),
            "labels": {"el": "Συχνές ερωτήσεις", "en": "FAQ"},
            "target": {"type": "content_page", "slug": "faq"},
        }
        assert second["target"] == {"type": "route", "route": "/products"}
        assert second["labels"]["el"] == "Προϊόντα"

    def test_a_content_page_link_with_no_english_label_carries_the_greek_title(
        self,
    ):
        # The legacy menu shows another language's title when a page has
        # none in the one asked for, so the import keeps the render.
        page = ContentPage.objects.create(slug="terms", is_published=True)
        page.create_translation("el", title="Όροι χρήσης")
        menu = NavigationMenu.objects.create(slot="header")
        NavigationLink.objects.create(menu=menu, content_page=page)

        (link,) = legacy.export_menu(menu, ["el", "en"])["links"]

        assert link["labels"] == {"el": "Όροι χρήσης", "en": "Όροι χρήσης"}

    def test_a_footer_menu_sends_its_columns(self):
        menu = NavigationMenu.objects.create(slot="footer")
        column = NavigationColumn.objects.create(
            menu=menu, icon="", shows_merchant_identity=True
        )
        column.create_translation("el", label="Εταιρεία")
        layout = make_layout("about")
        link = NavigationLink.objects.create(column=column, page_layout=layout)
        link.create_translation("el", label="Σχετικά")
        NavigationLink.objects.create(
            column=column, url="https://example.com/x"
        ).create_translation("el", label="Εξωτερικό")

        sent = legacy.export_menu(menu, ["el", "en"])

        (col,) = sent["columns"]
        assert col["labels"] == {"el": "Εταιρεία"}
        assert col["showsMerchantIdentity"] is True
        assert [link["target"] for link in col["links"]] == [
            {"type": "page_layout", "pageType": "about"},
            {"type": "url", "href": "https://example.com/x"},
        ]

    def test_the_stores_languages_come_default_first(self, tenant):
        tenant.available_locales = ["en", "el"]

        assert legacy.store_locales() == ["el", "en"]

    def test_a_single_language_store_sends_only_the_default(self, tenant):
        tenant.available_locales = []

        assert legacy.store_locales() == ["el"]


class TestImporting:
    def test_published_layouts_and_menus_become_one_release_and_unpublished_stay_drafts(
        self, converter
    ):
        make_layout("home")
        make_layout("about", published=False)
        menu = NavigationMenu.objects.create(slot="header")
        link = NavigationLink.objects.create(menu=menu, route="/blog")
        link.create_translation("el", label="Blog")

        report = importing.import_store()

        assert report.ok and report.applied
        assert sorted(report.documents) == [
            "group:header",
            "template:about",
            "template:home",
        ]
        assert sorted(report.published) == ["group:header", "template:home"]
        assert report.drafts == ["template:about"]
        release = BuilderRelease.objects.get()
        assert (release.number, release.note) == (1, importing.NOTE)
        assert release.published_by_staff_id is None
        assert set(release.versions) == {"group:header", "template:home"}
        about = BuilderDocument.objects.get(key="template:about")
        assert about.published_revision is None
        assert about.draft["order"]
        assert (
            BuilderDocument.objects.get(key="template:home").published_revision
            == 1
        )
        assert BuilderVersion.objects.count() == 2

    def test_a_page_type_the_catalogue_does_not_list_is_a_custom_page(
        self, converter
    ):
        make_layout("home")
        make_layout("spring-campaign")

        report = importing.import_store()

        assert sorted(report.documents) == [
            "page:spring-campaign",
            "template:home",
        ]
        kinds = {
            call.args[0]["key"]: call.args[0]["kind"]
            for call in converter.call_args_list
        }
        assert kinds == {
            "template:home": "template",
            "page:spring-campaign": "page",
        }
        assert (
            BuilderDocument.objects.get(key="page:spring-campaign").kind
            == "page"
        )

    def test_the_report_counts_each_documents_sections_and_blocks(self):
        make_layout("home")
        make_layout("about", published=False)
        nested = Converted(
            document={
                "sections": {
                    "h": {
                        "type": "hero@2",
                        "settings": {"title": "t"},
                        "blocks": {
                            "h-1": {"type": "slide@1", "settings": {}},
                            "h-2": {"type": "slide@1", "settings": {}},
                        },
                        "blockOrder": ["h-1", "h-2"],
                    },
                    "b": {"type": "banner@1", "settings": {"text": "x"}},
                },
                "order": ["h", "b"],
            },
            i18n={},
        )

        def convert(body):
            if body["key"] == "template:home":
                return nested
            return fake_convert(body)

        with mock.patch(
            "builder.importing.convert_legacy", side_effect=convert
        ):
            report = importing.import_store(dry_run=True)

        assert report.counts == {
            "template:home": {"sections": 2, "blocks": 2},
            "template:about": {"sections": 1, "blocks": 0},
        }
        assert asdict(report)["counts"] == report.counts

    def test_blocks_are_counted_at_every_depth(self):
        columns = {
            "sections": {
                "c": {
                    "type": "columns@1",
                    "settings": {},
                    "blocks": {
                        "c-1": {
                            "type": "column@1",
                            "settings": {},
                            "blocks": {
                                "c-1-1": {"type": "item@1", "settings": {}},
                                "c-1-2": {"type": "item@1", "settings": {}},
                            },
                            "blockOrder": ["c-1-1", "c-1-2"],
                        }
                    },
                    "blockOrder": ["c-1"],
                }
            },
            "order": ["c"],
        }

        assert importing.count_nodes(columns) == {"sections": 1, "blocks": 3}
        assert importing.count_nodes({"sections": {}, "order": []}) == {
            "sections": 0,
            "blocks": 0,
        }

    def test_the_seo_of_the_default_language_becomes_the_documents_settings(
        self, converter
    ):
        layout = make_layout("home")
        layout.create_translation("el", seo_title="Αρχική")

        importing.import_store()

        assert BuilderDocument.objects.get(key="template:home").draft[
            "settings"
        ] == {"seoTitle": "Αρχική"}

    def test_the_legacy_rows_are_left_alone(self, converter):
        make_layout("home")

        importing.import_store()

        assert PageLayout.objects.count() == 1
        assert PageSection.objects.count() == 1

    def test_warnings_are_reported_with_their_document(self, converter):
        menu = NavigationMenu.objects.create(slot="header")
        NavigationLink.objects.create(menu=menu, route="/blog")

        report = importing.import_store()

        assert report.warnings == [
            {
                "code": "note",
                "path": "",
                "message": "fake",
                "document": "group:header",
            }
        ]

    def test_a_dry_run_does_everything_and_keeps_nothing(self, converter):
        make_layout("home")

        report = importing.import_store(dry_run=True)

        assert report.ok and not report.applied
        assert report.release == 1
        assert not BuilderDocument.objects.exists()
        assert not BuilderRelease.objects.exists()
        assert not BuilderVersion.objects.exists()

    def test_a_store_with_builder_data_is_refused(self, converter):
        make_layout("home")
        importing.import_store()

        again = importing.import_store()

        assert [p["code"] for p in again.problems] == ["already_imported"]
        assert BuilderRelease.objects.count() == 1

    def test_a_store_with_nothing_to_import_releases_nothing(self, converter):
        report = importing.import_store()

        assert report.ok and report.documents == []
        assert not BuilderRelease.objects.exists()

    def test_a_layout_the_storefront_refuses_stops_the_whole_store(self):
        make_layout("home")
        make_layout("about")

        def refuse_about(body):
            if body["key"] == "template:about":
                raise ConversionRefused(
                    [
                        {
                            "path": "/sections/x",
                            "code": "unknown_block_type",
                            "message": "no such block",
                        }
                    ]
                )
            return fake_convert(body)

        with mock.patch(
            "builder.importing.convert_legacy", side_effect=refuse_about
        ):
            report = importing.import_store()

        assert not report.ok
        assert report.problems == [
            {
                "document": "template:about",
                "path": "/sections/x",
                "code": "unknown_block_type",
                "message": "no such block",
            }
        ]
        assert not BuilderDocument.objects.exists()

    def test_a_converted_document_that_does_not_validate_stops_the_store(self):
        make_layout("home")
        bad = Converted(
            document={
                "sections": {"a": {"type": "nothing@1", "settings": {}}},
                "order": ["a"],
            },
            i18n={},
        )

        with mock.patch("builder.importing.convert_legacy", return_value=bad):
            report = importing.import_store()

        assert [
            (p["document"], p["code"], p["path"]) for p in report.problems
        ] == [("template:home", "unknown_block_type", "/sections/a/type")]
        assert not BuilderDocument.objects.exists()

    def test_a_reference_to_nothing_stops_the_store_and_rolls_everything_back(
        self,
    ):
        make_layout("home")
        broken = Converted(
            document={
                "sections": {
                    "a": {
                        "type": "hero@2",
                        "settings": {"title": "t", "productId": 987654},
                    }
                },
                "order": ["a"],
            },
            i18n={},
        )

        with mock.patch(
            "builder.importing.convert_legacy", return_value=broken
        ):
            report = importing.import_store()

        assert [p["code"] for p in report.problems] == ["reference_not_found"]
        assert not BuilderDocument.objects.exists()
        assert not BuilderRelease.objects.exists()

    def test_a_feature_that_is_off_does_not_stop_the_import(self):
        make_layout("home")
        newsletter = Converted(
            document={
                "sections": {
                    "n": {
                        "type": "newsletter@1",
                        "settings": {"heading": "Join"},
                    }
                },
                "order": ["n"],
            },
            i18n={},
        )

        with (
            mock.patch(
                "builder.importing.convert_legacy", return_value=newsletter
            ),
            mock.patch("builder.services.feature_on", return_value=False),
        ):
            report = importing.import_store()

        assert report.ok

    def test_a_menu_may_point_at_a_page_imported_in_the_same_run(self):
        make_layout("about", published=False)
        NavigationMenu.objects.create(slot="header")

        def convert_pointing(page):
            def convert(body):
                if body["kind"] == "group":
                    return Converted(
                        document={
                            "sections": {
                                "h": {
                                    "type": "announcement@1",
                                    "settings": {"text": "t", "pageSlug": page},
                                }
                            },
                            "order": ["h"],
                        },
                        i18n={},
                    )
                return fake_convert(body)

            return convert

        with mock.patch(
            "builder.importing.convert_legacy",
            side_effect=convert_pointing("about"),
        ):
            ok = importing.import_store(dry_run=True)
        with mock.patch(
            "builder.importing.convert_legacy",
            side_effect=convert_pointing("nowhere"),
        ):
            broken = importing.import_store(dry_run=True)

        assert ok.ok
        assert [p["code"] for p in broken.problems] == ["reference_not_found"]


class TestTheConvertCall:
    BODY = {"kind": "template", "key": "template:home", "legacy": {"x": 1}}

    def response(self, status, body=None):
        reply = mock.Mock(spec=requests.Response)
        reply.status_code = status
        reply.json.return_value = body if body is not None else {}
        return reply

    def test_it_sends_the_request_with_the_shared_secret(self, settings):
        settings.NUXT_CACHE_PURGE_TOKEN = "s3cret"
        ok = self.response(
            200,
            {
                "document": {"sections": {}, "order": []},
                "i18n": {},
                "warnings": [],
            },
        )

        with mock.patch(
            "builder.convert.requests.post", return_value=ok
        ) as post:
            result = convert.convert_legacy(self.BODY)

        assert result.document == {"sections": {}, "order": []}
        (url,), kwargs = post.call_args
        assert url == "http://storefront.test/api/builder/convert-legacy"
        assert kwargs["headers"] == {convert.TOKEN_HEADER: "s3cret"}
        assert kwargs["json"]["defaultLocale"] == "el"
        assert kwargs["json"]["kind"] == "template"
        assert kwargs["json"]["schema"]

    def test_a_refusal_is_the_datas_fault_not_an_outage(self, settings):
        settings.NUXT_CACHE_PURGE_TOKEN = "s3cret"
        refusal = self.response(
            422, {"code": "invalid", "errors": [{"path": "/a", "code": "x"}]}
        )

        with (
            mock.patch("builder.convert.requests.post", return_value=refusal),
            pytest.raises(ConversionRefused) as caught,
        ):
            convert.convert_legacy(self.BODY)

        assert caught.value.errors == [{"path": "/a", "code": "x"}]

    @pytest.mark.parametrize(
        "failure",
        [
            requests.ConnectionError("down"),
            requests.Timeout("slow"),
        ],
    )
    def test_an_unreachable_storefront_is_unavailable(self, settings, failure):
        settings.NUXT_CACHE_PURGE_TOKEN = "s3cret"

        with (
            mock.patch("builder.convert.requests.post", side_effect=failure),
            pytest.raises(StorefrontUnavailable),
        ):
            convert.convert_legacy(self.BODY)

    @pytest.mark.parametrize("status", [401, 500, 503])
    def test_any_other_answer_is_unavailable(self, settings, status):
        settings.NUXT_CACHE_PURGE_TOKEN = "s3cret"

        with (
            mock.patch(
                "builder.convert.requests.post",
                return_value=self.response(status),
            ),
            pytest.raises(StorefrontUnavailable),
        ):
            convert.convert_legacy(self.BODY)

    def test_without_the_secret_nothing_is_sent(self, settings):
        settings.NUXT_CACHE_PURGE_TOKEN = ""

        with (
            mock.patch("builder.convert.requests.post") as post,
            pytest.raises(StorefrontUnavailable),
        ):
            convert.convert_legacy(self.BODY)

        post.assert_not_called()

    def test_a_reply_of_the_wrong_shape_is_unavailable(self, settings):
        settings.NUXT_CACHE_PURGE_TOKEN = "s3cret"

        with (
            mock.patch(
                "builder.convert.requests.post",
                return_value=self.response(200, {"nope": 1}),
            ),
            pytest.raises(StorefrontUnavailable),
        ):
            convert.convert_legacy(self.BODY)


def test_the_report_is_json_ready(converter):
    make_layout("home")

    report = importing.import_store(dry_run=True)

    assert json.loads(json.dumps(asdict(report)))["documents"] == [
        "template:home"
    ]
    assert BuilderReference.objects.count() == 0
