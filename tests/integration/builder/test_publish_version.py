"""``POST builder/documents/<key>/versions/<n>/publish``: one stored version
of one document goes live alone; the draft and every other document stay."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from builder.models import BuilderDocument, BuilderRelease
from product.factories.product import ProductFactory
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
ABOUT = "page:about"


def draft_url(key: str) -> str:
    return reverse("builder:document-draft", kwargs={"key": key})


def save(client, key, content, revision):
    response = client.put(
        draft_url(key),
        {"draft": content, "i18n": {}},
        format="json",
        headers={"If-Match": str(revision)},
    )
    assert response.status_code == status.HTTP_200_OK, response.json()


def publish(client, *keys):
    response = client.post(
        reverse("builder:publish"), {"documents": list(keys)}, format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED, response.json()


def publish_version(client, key, number, **body):
    return client.post(
        reverse(
            "builder:version-publish", kwargs={"key": key, "number": number}
        ),
        body,
        format="json",
    )


def read(page="home"):
    return (
        APIClient()
        .get(reverse("builder:published", kwargs={"page_key": page}))
        .json()
    )


@pytest.fixture
def client(admin, catalogue, features_on):
    return api_as(admin)


@pytest.fixture
def history(client):
    """Home published twice (v1 "Old", v2 "New") and about once, with an
    unpublished draft on top of home ("Draft")."""
    cache.clear()
    save(client, HOME, document(("h", hero("Old"))), 0)
    save(
        client,
        ABOUT,
        document(("b", section("banner@1", {"text": "About"}))),
        0,
    )
    publish(client, HOME, ABOUT)
    save(client, HOME, document(("h", hero("New"))), 1)
    publish(client, HOME)
    save(client, HOME, document(("h", hero("Draft"))), 2)
    return client


class TestPublishingOneVersion:
    def test_only_that_key_is_repointed(self, history):
        before = BuilderRelease.objects.order_by("-number").first()

        response = publish_version(history, HOME, 1, note="back to old")

        assert response.status_code == status.HTTP_201_CREATED
        release = BuilderRelease.objects.order_by("-number").first()
        assert release.number == before.number + 1
        assert release.note == "back to old"
        assert release.rolled_back_from is None
        changed = {
            key
            for key, version in release.versions.items()
            if before.versions.get(key) != version
        }
        assert changed == {HOME}
        assert read()["page"]["sections"]["h"]["settings"]["title"] == "Old"
        assert read("about")["page"]["key"] == ABOUT

    def test_the_draft_is_left_alone(self, history):
        before = BuilderDocument.objects.get(key=HOME)

        publish_version(history, HOME, 1)

        after = BuilderDocument.objects.get(key=HOME)
        assert after.draft == before.draft
        assert after.draft_revision == before.draft_revision
        assert after.updated_by_staff_id == before.updated_by_staff_id
        assert after.published_revision is None  # the draft is not published

    def test_the_document_is_published_when_the_draft_is_that_version(
        self, history
    ):
        # Restore v2 as the draft, then publish v2 again after v1 went live.
        publish_version(history, HOME, 1)
        history.post(
            reverse(
                "builder:version-restore", kwargs={"key": HOME, "number": 2}
            ),
            headers={"If-Match": "3"},
        )

        response = publish_version(history, HOME, 2)

        assert response.status_code == status.HTTP_201_CREATED
        document_ = BuilderDocument.objects.get(key=HOME)
        assert document_.published_revision == document_.draft_revision

    def test_the_references_index_follows_the_new_release(self, history):
        from builder.models import BuilderReference

        product = ProductFactory(active=True)
        save(
            history,
            HOME,
            document(("h", hero("Link", productId=product.pk))),
            3,
        )
        publish(history, HOME)  # version 3
        save(history, HOME, document(("h", hero("Plain"))), 4)
        publish(history, HOME)  # version 4
        used = BuilderReference.objects.filter(
            kind="product", target=str(product.pk)
        )
        assert not used.exists()

        publish_version(history, HOME, 3)

        assert list(used.values_list("document", flat=True)) == [HOME]

    def test_the_public_read_changes_at_once(self, history, tenant):
        assert read()["page"]["sections"]["h"]["settings"]["title"] == "New"

        publish_version(history, HOME, 1)
        # Every request loads the store row afresh; this suite binds one.
        tenant.refresh_from_db()

        assert read()["page"]["sections"]["h"]["settings"]["title"] == "Old"


class TestRefusing:
    def test_the_published_version_is_nothing_to_publish(self, history):
        response = publish_version(history, HOME, 2)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error = response.json()["errors"][0]
        assert error["code"] == "nothing_to_publish"
        assert error["document"] == HOME
        assert BuilderRelease.objects.count() == 2

    def test_a_version_whose_reference_is_gone_is_refused(self, history):
        from builder.models import BuilderVersion

        BuilderVersion.objects.filter(document__key=HOME, number=1).update(
            refs={"product": {"987654": ["/sections/h/settings/product"]}}
        )

        response = publish_version(history, HOME, 1)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error = response.json()["errors"][0]
        assert error["code"] == "reference_not_found"
        assert error["document"] == HOME
        assert BuilderRelease.objects.count() == 2

    def test_an_unknown_version_or_document_is_a_404(self, history):
        assert (
            publish_version(history, HOME, 99).status_code
            == status.HTTP_404_NOT_FOUND
        )
        assert (
            publish_version(history, "page:nowhere", 1).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_staff_who_only_look_and_visitors_may_not(self, history, staff):
        for who in (api_as(staff), APIClient()):
            response = publish_version(who, HOME, 1)

            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
        assert BuilderRelease.objects.count() == 2
