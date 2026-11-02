"""``POST builder/preview/resolve``: the editor's unsaved working copies,
resolved by the same code the public read uses, and stored nowhere."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from builder.models import BuilderDocument, BuilderRelease, BuilderVersion
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


def draft_url(key: str) -> str:
    return reverse("builder:document-draft", kwargs={"key": key})


def copy_of(*sections, i18n=None):
    return {"draft": document(*sections), "i18n": i18n or {}}


def preview(client, documents=None, *, page="home", **extra):
    return client.post(
        reverse("builder:preview-resolve"),
        {"pageKey": page, "documents": documents or {}, **extra},
        format="json",
    )


def without_versions(body):
    """The same answer with every document's ``version`` dropped."""
    out = dict(body)
    for slot in ("theme", "page"):
        if out.get(slot):
            out[slot] = {k: v for k, v in out[slot].items() if k != "version"}
    out["groups"] = {
        name: {k: v for k, v in doc.items() if k != "version"}
        for name, doc in out["groups"].items()
    }
    return out


@pytest.fixture
def client(admin, catalogue, features_on):
    return api_as(admin)


@pytest.fixture
def released(client):
    """A release of the home template, a header group and a theme."""
    cache.clear()
    drafts = {
        HOME: body(
            ("hero_1", hero("Καλώς ήρθατε", body="<p>ελ</p>")),
            ("banner_1", section("banner@1", {"text": "κρυφό"}, disabled=True)),
            i18n={
                "en": {"hero_1": {"/title": "Welcome", "/body": "<p>en</p>"}}
            },
        ),
        "group:header": body(
            ("ann", section("announcement@1", {"text": "Sale"}))
        ),
        "theme": body(("t", section("theme@1", {"radius": "lg"}))),
    }
    for key, content in drafts.items():
        client.put(
            draft_url(key), content, format="json", headers={"If-Match": "0"}
        )
    client.post(
        reverse("builder:publish"), {"documents": list(drafts)}, format="json"
    )
    return client


def body(*sections, i18n=None):
    return copy_of(*sections, i18n=i18n)


class TestSameCodeAsThePublicRead:
    def test_the_stored_drafts_preview_exactly_as_the_release_reads(
        self, released
    ):
        read = APIClient().get(
            reverse("builder:published", kwargs={"page_key": "home"}),
            {"locale": "en"},
        )
        copies = {
            key: {
                "draft": BuilderDocument.objects.get(key=key).draft,
                "i18n": BuilderDocument.objects.get(key=key).draft_i18n,
            }
            for key in (HOME, "group:header", "theme")
        }

        response = preview(released, copies, locale="en")

        assert response.status_code == status.HTTP_200_OK
        assert without_versions(response.json()) == without_versions(
            read.json()
        )
        assert response.json()["page"]["version"] == 0
        assert read.json()["page"]["version"] == 1

    def test_no_copies_is_the_release(self, released):
        read = APIClient().get(
            reverse("builder:published", kwargs={"page_key": "home"})
        )

        response = preview(released)

        assert response.json() == read.json()


class TestWorkingCopies:
    def test_a_copy_stands_in_for_the_released_document_of_its_key(
        self, released
    ):
        response = preview(
            released,
            {HOME: copy_of(("hero_1", hero("Νέος τίτλος")))},
            locale="el",
        )

        body_ = response.json()
        assert body_["page"]["sections"]["hero_1"]["settings"]["title"] == (
            "Νέος τίτλος"
        )
        assert body_["page"]["version"] == 0
        assert body_["release"] == 1
        assert list(body_["groups"]) == ["header"]
        assert body_["groups"]["header"]["version"] == 1
        assert body_["theme"]["version"] == 1

    def test_the_overlay_is_merged_and_disabled_nodes_are_dropped(
        self, released
    ):
        copy = copy_of(
            ("a", hero("Ελ")),
            ("b", section("banner@1", {"text": "κρυφό"}, disabled=True)),
            i18n={"en": {"a": {"/title": "En"}}},
        )

        english = preview(released, {HOME: copy}, locale="en").json()
        greek = preview(released, {HOME: copy}, locale="el").json()
        unknown = preview(released, {HOME: copy}, locale="xx").json()

        assert english["page"]["order"] == ["a"]
        assert english["page"]["sections"]["a"]["settings"]["title"] == "En"
        assert greek["page"]["sections"]["a"]["settings"]["title"] == "Ελ"
        assert unknown["locale"] == greek["locale"]

    def test_a_page_that_is_not_released_yet_can_be_previewed(self, released):
        response = preview(
            released,
            {"page:about": copy_of(("b", section("banner@1", {"text": "Hi"})))},
            page="about",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["page"]["key"] == "page:about"
        assert response.json()["theme"]["version"] == 1

    def test_a_store_page_wins_over_the_fixed_type_of_the_same_name(
        self, released
    ):
        response = preview(
            released,
            {"page:home": copy_of(("b", section("banner@1", {"text": "Own"})))},
        )

        assert response.json()["page"]["key"] == "page:home"

    def test_a_copy_of_a_group_replaces_or_adds_to_the_chrome(self, released):
        response = preview(
            released,
            {
                "group:header": copy_of(
                    ("ann", section("announcement@1", {"text": "New"}))
                ),
                "group:main_menu": copy_of(),
            },
        )

        groups = response.json()["groups"]
        assert list(groups) == ["header", "main_menu"]
        assert groups["header"]["sections"]["ann"]["settings"]["text"] == "New"

    def test_a_store_with_no_release_previews_its_copies(self, client):
        response = preview(
            client, {HOME: copy_of(("a", hero("First")))}, page="home"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["release"] == 0
        assert response.json()["theme"] is None

    def test_nothing_is_stored_or_cached(self, released):
        before = (
            BuilderDocument.objects.count(),
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
            BuilderDocument.objects.get(key=HOME).draft_revision,
        )
        cache.clear()

        preview(released, {HOME: copy_of(("a", hero("Unsaved")))})
        read = APIClient().get(
            reverse("builder:published", kwargs={"page_key": "home"})
        )

        assert before == (
            BuilderDocument.objects.count(),
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
            BuilderDocument.objects.get(key=HOME).draft_revision,
        )
        assert read.json()["page"]["sections"]["hero_1"]["settings"][
            "title"
        ] == ("Καλώς ήρθατε")

    def test_it_is_never_cached(self, released):
        response = preview(released)

        header = response["Cache-Control"]
        assert "private" in header
        assert "no-store" in header

    def test_a_key_is_not_camel_cased(self, released):
        response = preview(
            released,
            {"group:main_menu": copy_of()},
        )

        assert "main_menu" in response.json()["groups"]


class TestRefusing:
    def test_a_copy_is_validated_like_a_draft_save(self, released):
        bad = copy_of(("a", section("hero@2", {"nope": 1})))

        response = preview(released, {HOME: bad})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        errors = response.json()["errors"]
        assert response.json()["code"] == "invalid_document"
        assert errors
        assert {e["document"] for e in errors} == {HOME}
        assert all({"path", "code", "message"} <= e.keys() for e in errors)

    def test_every_problem_of_every_copy_is_reported(self, released):
        bad = copy_of(("a", section("hero@2", {"nope": 1})))

        response = preview(
            released, {HOME: bad, "page:about": bad, "bad key": bad}
        )

        documents = {e["document"] for e in response.json()["errors"]}
        assert documents == {HOME, "page:about", "bad key"}

    def test_an_unknown_page_is_a_404(self, released):
        response = preview(released, page="nowhere")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "page_not_published"

    def test_too_many_copies_are_refused(self, released, settings):
        settings.BUILDER_PREVIEW_MAX_DOCUMENTS = 1
        one = copy_of(("a", hero()))

        assert preview(released, {HOME: one}).status_code == status.HTTP_200_OK
        response = preview(released, {HOME: one, "page:about": one})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["errors"][0]["code"] == "too_many_documents"

    @pytest.mark.parametrize(
        "body_",
        [
            {},
            {"pageKey": ""},
            {"pageKey": "a b"},
            {"pageKey": "home", "documents": []},
        ],
    )
    def test_a_malformed_request_is_a_400(self, released, body_):
        response = released.post(
            reverse("builder:preview-resolve"), body_, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_catalogue_must_answer_for_a_copy(self, released):
        from unittest import mock

        from core.storefront import StorefrontUnavailable

        with mock.patch(
            "builder.services.storefront.fetch_catalogue",
            side_effect=StorefrontUnavailable(),
        ):
            response = preview(released, {HOME: copy_of(("a", hero()))})

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_no_copies_need_no_catalogue(self, released):
        from unittest import mock

        from core.storefront import StorefrontUnavailable

        with mock.patch(
            "builder.services.storefront.fetch_catalogue",
            side_effect=StorefrontUnavailable(),
        ):
            response = preview(released)

        assert response.status_code == status.HTTP_200_OK


class TestWho:
    def test_staff_who_only_look_may_preview(
        self, released, staff, catalogue, features_on
    ):
        response = preview(api_as(staff), {HOME: copy_of(("a", hero()))})

        assert response.status_code == status.HTTP_200_OK

    def test_a_shopper_and_a_visitor_may_not(self, released):
        from user.factories.account import UserAccountFactory

        for who in (api_as(UserAccountFactory()), APIClient()):
            response = preview(who)

            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )


class TestCost:
    def test_it_costs_a_fixed_number_of_queries(
        self, released, django_assert_max_num_queries
    ):
        copies = {HOME: copy_of(*[(f"a{i}", hero(f"T{i}")) for i in range(20)])}
        preview(released, copies)  # warm the catalogue and caches

        with django_assert_max_num_queries(8):
            response = preview(released, copies)

        assert response.status_code == status.HTTP_200_OK
