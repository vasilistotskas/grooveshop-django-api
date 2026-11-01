"""The storefront's golden fixtures, replayed against what Django sends and
reads.

``tests/fixtures/builder/legacy/*.json`` are copies of the storefront's
``test/fixtures/builder/legacy/`` (``{request, response}`` per case: one for
every ported section type, the cross-cutting cases and the three menus). The
converter itself is TypeScript and tested there; what these tests pin is
Django's side of the same contract:

* the request ``builder.legacy`` builds from the rows a store has equals the
  fixture's request, so a change to either side's shape fails here;
* the response parses through ``builder.convert`` into what the importer
  validates and stores.

To refresh the copies: copy the directory from the storefront repository and
run this file; a failing case is a contract change to agree on, not to paper
over.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import requests

from builder import convert, legacy
from page_config.models import (
    ContentPage,
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    PageLayout,
    PageSection,
)

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/builder/legacy"
CASES = sorted(FIXTURES.glob("*.json"))
SEO_FIELDS = {
    "seoTitle": "seo_title",
    "seoDescription": "seo_description",
    "seoKeywords": "seo_keywords",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def layout_cases() -> list[Path]:
    return [p for p in CASES if load(p)["request"]["kind"] != "group"]


def menu_cases() -> list[Path]:
    return [p for p in CASES if load(p)["request"]["kind"] == "group"]


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1)


def test_the_fixtures_are_there():
    assert len(CASES) >= 40


def build_layout(request: dict) -> PageLayout:
    data = request["legacy"]
    layout = PageLayout.objects.create(
        page_type=data["pageType"], title=data["title"], is_published=True
    )
    for code, seo in data["seo"].items():
        layout.create_translation(
            code, **{SEO_FIELDS[name]: value for name, value in seo.items()}
        )
    for position, section in enumerate(data["sections"]):
        PageSection.objects.create(
            layout=layout,
            uuid=section["id"],
            component_type=section["type"],
            title=section["title"],
            is_visible=section["isVisible"],
            props=section["props"],
            i18n=section.get("i18n", {}),
            sort_order=position,
        )
    return layout


@pytest.mark.parametrize("path", layout_cases(), ids=lambda p: p.stem)
def test_a_layout_is_sent_as_the_storefront_expects(path):
    request = load(path)["request"]
    layout = build_layout(request)

    sent = legacy.export_layout(layout)

    # Django leaves an empty SEO field out; a fixture may spell it "".
    expected = {
        **request["legacy"],
        "seo": {
            code: {name: v for name, v in fields.items() if v}
            for code, fields in request["legacy"]["seo"].items()
        },
    }
    assert canonical(sent) == canonical(expected)


def build_links(parent: dict, owner_field: str, owner) -> None:
    for position, link in enumerate(parent["links"]):
        target = link["target"]
        fields: dict = {owner_field: owner, "sort_order": position}
        if target["type"] == "content_page":
            page, _ = ContentPage.objects.get_or_create(
                slug=target["slug"], defaults={"is_published": True}
            )
            fields["content_page"] = page
        elif target["type"] == "page_layout":
            layout, _ = PageLayout.objects.get_or_create(
                page_type=target["pageType"],
                defaults={"title": link["labels"]["el"], "is_published": True},
            )
            fields["page_layout"] = layout
        elif target["type"] == "route":
            fields["route"] = target["route"]
        else:
            fields["url"] = target["href"]
        ids = {"uuid": link["id"]} if "id" in link else {}
        row = NavigationLink.objects.create(**ids, **fields)
        for code, label in link["labels"].items():
            if label:
                row.create_translation(code, label=label)


def build_menu(request: dict) -> NavigationMenu:
    data = request["legacy"]
    ids = {"uuid": data["id"]} if "id" in data else {}
    menu = NavigationMenu.objects.create(slot=data["slot"], **ids)
    for position, column in enumerate(data.get("columns", [])):
        row = NavigationColumn.objects.create(
            uuid=column["id"],
            menu=menu,
            icon=column.get("icon", ""),
            shows_merchant_identity=column.get("showsMerchantIdentity", False),
            sort_order=position,
        )
        for code, label in column["labels"].items():
            row.create_translation(code, label=label)
        build_links(column, "column", row)
    if "links" in data:
        build_links(data, "menu", menu)
    return menu


def structure(data: dict, *, ids: bool = True) -> list:
    """Everything a menu request says except the link labels, which the
    legacy menu fills from the page's own title when a link has no label of
    its own (so a fixture's empty label is not what the rows produce), and
    a menu-level link's icon, which a NavigationLink has no field for."""
    columns = "columns" in data
    return [
        (
            entry.get("id") if ids else None,
            entry.get("icon", "") if columns else "",
            entry.get("showsMerchantIdentity", False),
            entry.get("target"),
            [
                (link.get("id") if ids else None, link["target"])
                for link in entry.get("links", [])
            ],
        )
        for entry in data.get("columns") or data.get("links") or []
    ]


@pytest.mark.parametrize("path", menu_cases(), ids=lambda p: p.stem)
def test_a_menu_is_sent_as_the_storefront_expects(path):
    request = load(path)["request"]
    menu = build_menu(request)

    sent = legacy.export_menu(menu, ["el", "en"])

    assert sent["slot"] == request["legacy"]["slot"]
    assert sent["id"] == request["legacy"].get("id", sent["id"])
    expected = request["legacy"]
    # A fixture may leave a link's id out (ids are optional on the wire).
    entries = expected.get("columns") or expected.get("links") or []
    ids = all("id" in entry for entry in entries)
    assert structure(sent, ids=ids) == structure(expected, ids=ids)


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_a_response_parses_into_what_the_importer_validates(path, settings):
    settings.NUXT_CACHE_PURGE_TOKEN = "token"
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    fixture = load(path)
    reply = mock.Mock(spec=requests.Response)
    reply.status_code = 200
    reply.json.return_value = fixture["response"]

    with mock.patch("builder.convert.requests.post", return_value=reply):
        converted = convert.convert_legacy(fixture["request"])

    assert converted.document == fixture["response"]["document"]
    assert converted.i18n == fixture["response"]["i18n"]
    assert converted.warnings == fixture["response"]["warnings"]
    assert set(converted.document) <= {"settings", "sections", "order"}
    assert list(converted.document["sections"]) == converted.document["order"]
