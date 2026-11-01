"""The builder's staff API and the public read of the current release."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


def draft_url(key: str = HOME) -> str:
    return reverse("builder:document-draft", kwargs={"key": key})


def put(client, body, *, revision="0", key: str = HOME):
    headers = {} if revision is None else {"headers": {"If-Match": revision}}
    return client.put(draft_url(key), body, format="json", **headers)


def body_of(*sections, i18n=None):
    return {"draft": document(*sections), "i18n": i18n or {}}


@pytest.fixture
def client(admin, catalogue, features_on):
    return api_as(admin)


class TestDraft:
    def test_the_content_travels_without_being_recased(self, client):
        sent = body_of(("hero_1", hero(productId=4)))

        response = put(client, sent)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["draft"] == sent["draft"]
        assert response["ETag"] == '"1"'
        assert response.json()["draftRevision"] == 1

        again = client.get(
            reverse("builder:document-detail", kwargs={"key": HOME})
        )
        assert again.json()["draft"] == sent["draft"]
        assert again["ETag"] == '"1"'

    def test_the_overlay_keeps_its_key(self, client):
        sent = body_of(
            ("hero_1", hero("Καλώς")),
            i18n={"en": {"hero_1": {"/title": "Welcome"}}},
        )

        response = put(client, sent)

        assert response.json()["i18n"] == sent["i18n"]

    def test_if_match_is_required_and_must_be_a_number(self, client):
        missing = put(client, body_of(("a", hero())), revision=None)
        junk = put(client, body_of(("a", hero())), revision="abc")

        assert missing.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert missing.json()["code"] == "precondition_required"
        assert junk.status_code == status.HTTP_400_BAD_REQUEST
        assert junk.json()["code"] == "invalid_revision"

    @pytest.mark.parametrize("header", ['"1"', 'W/"1"', "1"])
    def test_every_spelling_of_the_etag_is_understood(self, client, header):
        put(client, body_of(("a", hero())))

        response = put(client, body_of(("a", hero("Other"))), revision=header)

        assert response.status_code == status.HTTP_200_OK

    def test_a_conflict_names_the_current_revision_and_who_saved(
        self, client, admin
    ):
        put(client, body_of(("a", hero())))
        put(client, body_of(("a", hero("two"))), revision="1")

        response = put(client, body_of(("a", hero("mine"))), revision="1")

        assert response.status_code == status.HTTP_409_CONFLICT
        body = response.json()
        assert body["code"] == "revision_conflict"
        assert body["currentRevision"] == 2
        assert body["updatedBy"]["id"] == admin.pk

    def test_problems_are_listed_with_pointers_and_codes(self, client):
        response = put(
            client,
            body_of(
                ("a", section("hero@2", {})),
                ("b", section("nothing@1")),
            ),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["code"] == "invalid_document"
        assert {(e["path"], e["code"]) for e in body["errors"]} == {
            ("/sections/a/settings", "invalid_settings"),
            ("/sections/b/type", "unknown_block_type"),
        }

    def test_the_body_must_have_a_draft(self, client):
        response = put(client, {"i18n": {}})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "draft" in response.json()["fields"]

    def test_an_unreachable_storefront_is_a_503(self, client, catalogue):
        catalogue.status = 502

        response = put(client, body_of(("a", hero())))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "storefront_unavailable"


class TestLifecycle:
    def test_checkpoint_versions_restore_discard(self, client):
        put(client, body_of(("a", hero("one"))))
        checkpoint = client.post(
            reverse("builder:document-checkpoint", kwargs={"key": HOME}),
            {"note": "first"},
            format="json",
        )
        put(client, body_of(("a", hero("two"))), revision="1")

        versions = client.get(
            reverse("builder:version-list", kwargs={"key": HOME})
        )
        detail = client.get(
            reverse("builder:version-detail", kwargs={"key": HOME, "number": 1})
        )
        restored = client.post(
            reverse(
                "builder:version-restore", kwargs={"key": HOME, "number": 1}
            ),
            headers={"If-Match": "2"},
        )
        discarded = client.post(
            reverse("builder:document-discard", kwargs={"key": HOME}),
            headers={"If-Match": "3"},
        )

        assert checkpoint.status_code == status.HTTP_201_CREATED
        assert checkpoint.json()["note"] == "first"
        assert versions.json()["results"][0]["number"] == 1
        assert (
            detail.json()["data"]["sections"]["a"]["settings"]["title"] == "one"
        )
        assert (
            restored.json()["draft"]["sections"]["a"]["settings"]["title"]
            == "one"
        )
        assert discarded.json()["draftRevision"] == 4

    def test_publish_lists_releases_and_rolls_back(self, client):
        put(client, body_of(("a", hero("one"))))
        first = client.post(
            reverse("builder:publish"),
            {"documents": [HOME], "note": "v1"},
            format="json",
        )
        put(client, body_of(("a", hero("two"))), revision="1")
        client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

        releases = client.get(reverse("builder:release-list"))
        rolled = client.post(
            reverse("builder:rollback"),
            {"release": first.json()["id"]},
            format="json",
        )
        listing = client.get(reverse("builder:document-list"))

        assert first.status_code == status.HTTP_201_CREATED
        assert first.json()["note"] == "v1"
        assert [r["number"] for r in releases.json()["results"]] == [2, 1]
        assert rolled.status_code == status.HTTP_201_CREATED
        assert rolled.json()["rolledBackFrom"] == 1
        (row,) = listing.json()["results"]
        assert row["key"] == HOME
        assert row["publishedVersion"] == 1
        assert row["hasUnpublishedChanges"] is True

    def test_a_failed_publish_names_the_document(self, client):
        client.put(
            draft_url("group:header"),
            body_of(("n", section("announcement@1", {"text": "x"}))),
            format="json",
            headers={"If-Match": "0"},
        )
        put(client, body_of(("a", hero(productId=987654))))

        response = client.post(
            reverse("builder:publish"),
            {"documents": [HOME, "group:header"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert [
            (e["document"], e["code"]) for e in response.json()["errors"]
        ] == [(HOME, "reference_not_found")]

    def test_a_page_is_deleted_with_if_match(self, client):
        banner = body_of(("b", section("banner@1", {"text": "x"})))
        put(client, banner, key="page:about")
        url = reverse("builder:document-detail", kwargs={"key": "page:about"})

        refused = client.delete(url)
        deleted = client.delete(url, headers={"If-Match": "1"})

        assert refused.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert deleted.status_code == status.HTTP_204_NO_CONTENT
        assert client.get(url).status_code == status.HTTP_404_NOT_FOUND

    def test_the_list_can_be_narrowed_by_kind(self, client):
        put(client, body_of(("a", hero())))
        put(
            client,
            body_of(("n", section("announcement@1", {"text": "x"}))),
            key="group:header",
        )

        response = client.get(
            reverse("builder:document-list"), {"kind": "group"}
        )

        assert [r["key"] for r in response.json()["results"]] == [
            "group:header"
        ]


class TestWhoMay:
    def test_staff_may_look_but_not_touch(
        self, admin, staff, catalogue, features_on
    ):
        put(api_as(admin), body_of(("a", hero())))
        reader = api_as(staff)
        before = {
            "list": reader.get(reverse("builder:document-list")),
            "detail": reader.get(
                reverse("builder:document-detail", kwargs={"key": HOME})
            ),
            "releases": reader.get(reverse("builder:release-list")),
        }
        writes = [
            put(reader, body_of(("a", hero("x"))), revision="1"),
            reader.post(
                reverse("builder:publish"), {"documents": [HOME]}, format="json"
            ),
            reader.post(
                reverse("builder:rollback"), {"release": 1}, format="json"
            ),
            reader.post(
                reverse("builder:document-checkpoint", kwargs={"key": HOME}),
                {},
                format="json",
            ),
        ]

        assert {r.status_code for r in before.values()} == {status.HTTP_200_OK}
        assert {r.status_code for r in writes} == {status.HTTP_403_FORBIDDEN}

    def test_a_shopper_and_a_visitor_may_do_nothing(
        self, catalogue, features_on
    ):
        from user.factories.account import UserAccountFactory

        shopper = api_as(UserAccountFactory())
        visitor = APIClient()

        for who in (shopper, visitor):
            assert who.get(reverse("builder:document-list")).status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
            assert put(who, body_of(("a", hero()))).status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )


class TestPublishedRead:
    @pytest.fixture
    def released(self, client, catalogue):
        cache.clear()
        put(
            client,
            body_of(
                ("hero_1", hero("Καλώς ήρθατε", body="<p>ελ</p>")),
                (
                    "banner_1",
                    section("banner@1", {"text": "κρυφό"}, disabled=True),
                ),
                (
                    "banner_2",
                    section("banner@1", {"text": "Μπάνερ"}),
                ),
                i18n={
                    "en": {
                        "hero_1": {"/title": "Welcome", "/body": "<p>en</p>"},
                        "banner_2": {"/text": "Banner"},
                    }
                },
            ),
        )
        client.put(
            draft_url("group:header"),
            body_of(("ann", section("announcement@1", {"text": "Sale"}))),
            format="json",
            headers={"If-Match": "0"},
        )
        client.put(
            draft_url("theme"),
            body_of(("t", section("theme@1", {"radius": "lg"}))),
            format="json",
            headers={"If-Match": "0"},
        )
        client.post(
            reverse("builder:publish"),
            {"documents": [HOME, "group:header", "theme"]},
            format="json",
        )
        return client

    def read(self, page="home", locale=None):
        query = {"locale": locale} if locale else {}
        return APIClient().get(
            reverse("builder:published", kwargs={"page_key": page}), query
        )

    def test_it_resolves_the_default_language_and_drops_disabled_nodes(
        self, released
    ):
        response = self.read(locale="el")

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["release"] == 1
        assert body["locale"] == "el"
        assert body["page"]["key"] == HOME
        assert body["page"]["order"] == ["hero_1", "banner_2"]
        assert body["page"]["sections"]["hero_1"]["settings"]["title"] == (
            "Καλώς ήρθατε"
        )
        assert "banner_1" not in body["page"]["sections"]
        assert body["theme"]["sections"]["t"]["settings"] == {"radius": "lg"}
        assert list(body["groups"]) == ["header"]
        assert "disabled" not in body["page"]["sections"]["hero_1"]

    def test_it_merges_the_overlay_for_the_language_asked(self, released):
        body = self.read(locale="en").json()

        sections = body["page"]["sections"]
        assert sections["hero_1"]["settings"]["title"] == "Welcome"
        assert sections["hero_1"]["settings"]["body"] == "<p>en</p>"
        assert sections["banner_2"]["settings"]["text"] == "Banner"

    def test_an_unknown_language_is_the_default_one(self, released):
        assert self.read(locale="fr").json()["locale"] == "el"
        assert (
            self.read().json()["page"]["sections"]["banner_2"]["settings"][
                "text"
            ]
            == "Μπάνερ"
        )

    def test_the_draft_is_never_what_is_served(self, released):
        put(released, body_of(("hero_1", hero("DRAFT"))), revision="1")

        body = self.read(locale="el").json()

        assert body["page"]["sections"]["hero_1"]["settings"]["title"] == (
            "Καλώς ήρθατε"
        )

    def test_a_new_release_is_served_at_once(self, released, tenant):
        self.read(locale="el")
        put(released, body_of(("hero_1", hero("v2"))), revision="1")
        released.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )
        # Every request loads the store row afresh; this suite binds one.
        tenant.refresh_from_db()

        body = self.read(locale="el").json()

        assert body["release"] == 2
        assert body["page"]["sections"]["hero_1"]["settings"]["title"] == "v2"

    def test_a_page_wins_over_a_template_of_the_same_name(self, released):
        released.put(
            draft_url("page:home"),
            body_of(("b", section("banner@1", {"text": "page"}))),
            format="json",
            headers={"If-Match": "0"},
        )
        released.post(
            reverse("builder:publish"),
            {"documents": ["page:home"]},
            format="json",
        )

        assert self.read().json()["page"]["key"] == "page:home"

    def test_something_unreleased_is_a_404(self, released):
        response = self.read("product")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "page_not_published"

    def test_a_store_with_no_release_is_a_404(self, catalogue, features_on):
        cache.clear()

        assert self.read().status_code == status.HTTP_404_NOT_FOUND

    def test_a_warm_read_does_not_touch_the_database(
        self, released, django_assert_num_queries
    ):
        self.read(locale="el")

        with django_assert_num_queries(0):
            response = self.read(locale="el")

        assert response.status_code == status.HTTP_200_OK
