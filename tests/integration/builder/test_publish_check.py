"""``POST builder/publish/check`` answers what a publish would, writing
nothing; a release lists its documents with version numbers."""

from __future__ import annotations

from unittest import mock

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from builder.models import BuilderDocument, BuilderRelease, BuilderVersion
from core.storefront import StorefrontUnavailable
from product.factories.product import ProductFactory
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
ABOUT = "page:about"
HEADER = "group:header"


def save(client, key, content, revision=0):
    response = client.put(
        reverse("builder:document-draft", kwargs={"key": key}),
        {"draft": content, "i18n": {}},
        format="json",
        headers={"If-Match": str(revision)},
    )
    assert response.status_code == status.HTTP_200_OK, response.json()


def about(text="About"):
    return document(("b", section("banner@1", {"text": text})))


def check(client, *keys):
    return client.post(
        reverse("builder:publish-check"),
        {"documents": list(keys)},
        format="json",
    )


def publish(client, *keys):
    return client.post(
        reverse("builder:publish"), {"documents": list(keys)}, format="json"
    )


@pytest.fixture
def client(admin, catalogue, features_on):
    return api_as(admin)


@pytest.fixture
def drafts(client):
    save(client, HOME, document(("h", hero("Home"))))
    save(client, ABOUT, about())
    return client


class TestChecking:
    def test_clean_drafts_are_ok_and_all_changed(self, drafts):
        response = check(drafts, HOME, ABOUT)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "ok": True,
            "changed": [HOME, ABOUT],
            "problems": [],
        }

    def test_it_writes_nothing(self, drafts):
        before = (
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
            dict(BuilderDocument.objects.values_list("key", "draft_revision")),
            dict(
                BuilderDocument.objects.values_list("key", "published_revision")
            ),
        )

        check(drafts, HOME, ABOUT)

        assert before == (
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
            dict(BuilderDocument.objects.values_list("key", "draft_revision")),
            dict(
                BuilderDocument.objects.values_list("key", "published_revision")
            ),
        )

    def test_only_what_differs_from_the_release_is_changed(self, drafts):
        publish(drafts, HOME, ABOUT)
        save(drafts, ABOUT, about("Edited"), revision=1)

        response = check(drafts, HOME, ABOUT)

        assert response.json() == {
            "ok": True,
            "changed": [ABOUT],
            "problems": [],
        }

    def test_nothing_changed_is_a_problem_as_in_a_publish(self, drafts):
        publish(drafts, HOME, ABOUT)

        checked = check(drafts, HOME, ABOUT).json()
        published = publish(drafts, HOME, ABOUT)

        assert checked["ok"] is False
        assert checked["changed"] == []
        assert [p["code"] for p in checked["problems"]] == [
            "nothing_to_publish"
        ]
        assert published.status_code == status.HTTP_400_BAD_REQUEST
        assert published.json()["errors"][0]["code"] == "nothing_to_publish"

    def test_a_draft_edited_back_to_the_released_content_is_not_changed(
        self, drafts
    ):
        publish(drafts, ABOUT)
        save(drafts, ABOUT, about("Edited"), revision=1)
        save(drafts, ABOUT, about(), revision=2)

        response = check(drafts, ABOUT).json()

        assert response["changed"] == []
        assert response["problems"][0]["code"] == "nothing_to_publish"

    def test_it_reports_what_the_publish_would_refuse_with_the_document(
        self, drafts
    ):
        BuilderDocument.objects.filter(key=HOME).update(
            draft=document(("h", section("hero@2", {"nope": 1})))
        )

        response = check(drafts, HOME, ABOUT).json()
        refused = publish(drafts, HOME, ABOUT).json()["errors"]

        assert response["ok"] is False
        assert response["changed"] == [ABOUT]
        assert {p["document"] for p in response["problems"]} == {HOME}
        assert all(
            {"document", "path", "code", "message"} <= p.keys()
            for p in response["problems"]
        )
        assert [
            (p["document"], p["path"], p["code"]) for p in response["problems"]
        ] == [(e["document"], e["path"], e["code"]) for e in refused]

    def test_a_reference_that_is_gone_is_a_problem(self, drafts):
        product = ProductFactory(active=True)
        save(
            drafts,
            HOME,
            document(("h", hero("Link", productId=product.pk))),
            revision=1,
        )
        product.delete()

        response = check(drafts, HOME).json()

        assert response["ok"] is False
        (problem,) = response["problems"]
        assert problem["code"] == "reference_not_found"
        assert problem["document"] == HOME
        assert problem["params"]["kind"] == "product"

    def test_an_unknown_document_is_a_404(self, drafts):
        response = check(drafts, HOME, "page:nowhere")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_no_documents_is_a_400(self, drafts):
        assert check(drafts).status_code == status.HTTP_400_BAD_REQUEST

    def test_it_needs_the_catalogue_like_a_publish(self, drafts):
        with mock.patch(
            "builder.services.storefront.fetch_catalogue",
            side_effect=StorefrontUnavailable(),
        ):
            response = check(drafts, HOME)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_it_costs_a_fixed_number_of_queries(
        self, drafts, django_assert_max_num_queries
    ):
        check(drafts, HOME, ABOUT)

        with django_assert_max_num_queries(12):
            response = check(drafts, HOME, ABOUT)

        assert response.status_code == status.HTTP_200_OK


class TestWho:
    def test_staff_who_only_look_cannot_check(self, drafts, staff):
        response = check(api_as(staff), HOME)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_visitor_cannot(self, drafts):
        response = check(APIClient(), HOME)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestReleaseDocuments:
    def releases(self, client):
        results = client.get(reverse("builder:release-list")).json()["results"]
        return {r["number"]: r for r in results}

    def test_a_release_lists_its_documents_with_version_numbers(self, drafts):
        save(
            drafts,
            HEADER,
            document(("a", section("announcement@1", {"text": "Sale"}))),
        )
        publish(drafts, HOME, HEADER)
        save(drafts, HOME, document(("h", hero("Home v2"))), revision=1)
        publish(drafts, HOME)

        releases = self.releases(drafts)

        assert releases[1]["documents"] == [
            {"key": HEADER, "versionNumber": 1, "changed": True},
            {"key": HOME, "versionNumber": 1, "changed": True},
        ]
        assert releases[2]["documents"] == [
            {"key": HEADER, "versionNumber": 1, "changed": False},
            {"key": HOME, "versionNumber": 2, "changed": True},
        ]

    def test_a_rollback_changes_what_it_moves_back(self, drafts):
        publish(drafts, HOME)
        save(drafts, HOME, document(("h", hero("v2"))), revision=1)
        publish(drafts, HOME)
        first = BuilderRelease.objects.get(number=1)

        rolled = drafts.post(
            reverse("builder:rollback"), {"release": first.pk}, format="json"
        ).json()

        assert rolled["documents"] == [
            {"key": HOME, "versionNumber": 1, "changed": True}
        ]

    def test_the_publish_answer_carries_them_too(self, drafts):
        body = publish(drafts, HOME, ABOUT).json()

        assert body["documents"] == [
            {"key": ABOUT, "versionNumber": 1, "changed": True},
            {"key": HOME, "versionNumber": 1, "changed": True},
        ]
        assert body["versions"]

    def test_listing_releases_costs_a_fixed_number_of_queries(
        self, drafts, django_assert_max_num_queries
    ):
        # ``drafts`` saved HOME at revision 1; each edit and publish adds a release.
        for revision in range(1, 6):
            save(
                drafts,
                HOME,
                document(("h", hero(f"v{revision}"))),
                revision=revision,
            )
            publish(drafts, HOME)
        url = reverse("builder:release-list")
        drafts.get(url)

        with django_assert_max_num_queries(10):
            response = drafts.get(url)

        assert len(response.json()["results"]) == 5
