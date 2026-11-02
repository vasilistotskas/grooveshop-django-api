"""Share-preview links: unpublished drafts shown to someone without an
account, through an unguessable token that opens nothing once it lapses."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from builder import preview_links
from builder.models import (
    BuilderPreviewLink,
    BuilderRelease,
    BuilderVersion,
)
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero, section

pytestmark = pytest.mark.django_db

HOME = "template:home"
ABOUT = "page:about"


def save(client, key, content, revision=0):
    response = client.put(
        reverse("builder:document-draft", kwargs={"key": key}),
        {"draft": content, "i18n": {}},
        format="json",
        headers={"If-Match": str(revision)},
    )
    assert response.status_code == status.HTTP_200_OK, response.json()


def share(client, *keys, note=""):
    return client.post(
        reverse("builder:preview-link-list"),
        {"documents": list(keys), "note": note},
        format="json",
    )


def open_link(token, page="home", **query):
    return APIClient().get(
        reverse(
            "builder:preview-shared",
            kwargs={"token": token, "page_key": page},
        ),
        query,
    )


@pytest.fixture
def client(admin, catalogue, features_on):
    cache.clear()
    return api_as(admin)


@pytest.fixture
def draft(client):
    """An unpublished home draft."""
    save(client, HOME, document(("h", hero("Draft title"))))
    return client


def title(response):
    return response.json()["page"]["sections"]["h"]["settings"]["title"]


class TestMaking:
    def test_it_answers_a_token_once_and_stores_only_its_hash(self, draft):
        response = share(draft, HOME, note="for Maria")

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert len(body["token"]) >= 43  # 256 random bits
        assert body["documents"] == [HOME]
        assert body["note"] == "for Maria"
        assert body["expiresAt"]
        link = BuilderPreviewLink.objects.get()
        assert link.token_hash == preview_links.hash_token(body["token"])
        assert body["token"] not in str(link.__dict__)
        assert body["token"] not in str(link.snapshots)

    def test_the_lifetime_comes_from_the_setting(self, draft, settings):
        settings.BUILDER_PREVIEW_LINK_TTL_SECONDS = 3600

        share(draft, HOME)

        left = BuilderPreviewLink.objects.get().expires_at - timezone.now()
        assert timedelta(minutes=59) < left <= timedelta(hours=1)

    def test_it_is_outside_the_version_history(self, draft):
        before = (
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
        )

        share(draft, HOME)

        assert before == (
            BuilderVersion.objects.count(),
            BuilderRelease.objects.count(),
        )

    def test_two_links_have_two_tokens(self, draft):
        first = share(draft, HOME).json()["token"]
        second = share(draft, HOME).json()["token"]

        assert first != second


class TestOpening:
    def test_anyone_sees_the_frozen_draft_without_signing_in(self, draft):
        token = share(draft, HOME).json()["token"]

        response = open_link(token)

        assert response.status_code == status.HTTP_200_OK
        assert title(response) == "Draft title"
        assert response.json()["page"]["version"] == 0
        assert response.json()["release"] == 0

    def test_the_answer_is_never_cached_or_indexed(self, draft):
        token = share(draft, HOME).json()["token"]

        response = open_link(token)

        assert "private" in response["Cache-Control"]
        assert "no-store" in response["Cache-Control"]
        assert "noindex" in response["X-Robots-Tag"]

    def test_later_edits_do_not_change_what_was_shared(self, draft):
        token = share(draft, HOME).json()["token"]

        save(draft, HOME, document(("h", hero("Edited later"))), revision=1)

        assert title(open_link(token)) == "Draft title"

    def test_the_rest_of_the_page_is_the_current_release(self, draft):
        save(
            draft,
            "group:header",
            document(("ann", section("announcement@1", {"text": "Sale"}))),
        )
        draft.post(
            reverse("builder:publish"),
            {"documents": ["group:header"]},
            format="json",
        )
        token = share(draft, HOME).json()["token"]

        body = open_link(token).json()

        assert body["release"] == 1
        assert list(body["groups"]) == ["header"]
        assert title(open_link(token)) == "Draft title"

    def test_a_publish_after_sharing_does_not_replace_the_shared_copy(
        self, draft
    ):
        token = share(draft, HOME).json()["token"]
        save(draft, HOME, document(("h", hero("Published"))), revision=1)
        draft.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

        assert title(open_link(token)) == "Draft title"

    def test_only_the_shared_documents_are_frozen(self, draft):
        save(draft, ABOUT, document(("b", section("banner@1", {"text": "x"}))))
        token = share(draft, HOME).json()["token"]

        assert open_link(token, "home").status_code == status.HTTP_200_OK
        response = open_link(token, "about")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "page_not_published"

    def test_the_locale_is_honoured(self, client):
        client.put(
            reverse("builder:document-draft", kwargs={"key": HOME}),
            {
                "draft": document(("h", hero("El"))),
                "i18n": {"en": {"h": {"/title": "En"}}},
            },
            format="json",
            headers={"If-Match": "0"},
        )
        token = share(client, HOME).json()["token"]

        assert title(open_link(token, locale="en")) == "En"
        assert title(open_link(token, locale="el")) == "El"


class TestEnding:
    def test_unknown_expired_and_revoked_are_one_answer(self, draft):
        expired = share(draft, HOME).json()
        revoked = share(draft, HOME).json()
        BuilderPreviewLink.objects.filter(pk=expired["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        draft.delete(
            reverse("builder:preview-link-detail", kwargs={"pk": revoked["id"]})
        )

        answers = [
            open_link(expired["token"]),
            open_link(revoked["token"]),
            open_link("x" * 43),
        ]

        assert {r.status_code for r in answers} == {status.HTTP_404_NOT_FOUND}
        assert {r.json()["code"] for r in answers} == {"preview_link_expired"}
        assert len({r.content for r in answers}) == 1

    def test_revoking_is_for_the_person_who_may_change_pages(self, draft):
        link = share(draft, HOME).json()
        url = reverse("builder:preview-link-detail", kwargs={"pk": link["id"]})

        assert draft.delete(url).status_code == status.HTTP_204_NO_CONTENT
        assert draft.delete(url).status_code == status.HTTP_404_NOT_FOUND
        assert not BuilderPreviewLink.objects.exists()

    def test_the_sweep_removes_only_the_expired(self, draft):
        old = share(draft, HOME).json()
        keep = share(draft, HOME).json()
        BuilderPreviewLink.objects.filter(pk=old["id"]).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        assert preview_links.purge_expired() == 1

        assert list(
            BuilderPreviewLink.objects.values_list("pk", flat=True)
        ) == [keep["id"]]


class TestListing:
    def test_it_lists_the_open_links_without_their_token(self, draft):
        made = share(draft, HOME, note="n").json()
        expired = share(draft, HOME).json()
        BuilderPreviewLink.objects.filter(pk=expired["id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = draft.get(reverse("builder:preview-link-list"))

        assert response.status_code == status.HTTP_200_OK
        (row,) = response.json()
        assert row["id"] == made["id"]
        assert row["documents"] == [HOME]
        assert row["note"] == "n"
        assert row["createdBy"]["id"]
        assert "token" not in row

    def test_it_costs_a_fixed_number_of_queries(
        self, draft, django_assert_max_num_queries
    ):
        for _ in range(4):
            share(draft, HOME)
        draft.get(reverse("builder:preview-link-list"))

        with django_assert_max_num_queries(8):
            response = draft.get(reverse("builder:preview-link-list"))

        assert len(response.json()) == 4


class TestRefusing:
    def test_a_draft_that_would_not_save_cannot_be_shared(self, draft):
        from builder.models import BuilderDocument

        BuilderDocument.objects.filter(key=HOME).update(
            draft=document(("h", section("hero@2", {"nope": 1})))
        )

        response = share(draft, HOME)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        errors = response.json()["errors"]
        assert {e["document"] for e in errors} == {HOME}
        assert not BuilderPreviewLink.objects.exists()

    def test_an_unknown_document_is_a_404(self, draft):
        assert (
            share(draft, "page:nowhere").status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_something_to_share_is_required(self, draft):
        response = draft.post(
            reverse("builder:preview-link-list"),
            {"documents": []},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_number_of_documents_and_links_is_limited(
        self, draft, settings
    ):
        settings.BUILDER_PREVIEW_MAX_DOCUMENTS = 1
        save(draft, ABOUT, document(("b", section("banner@1", {"text": "x"}))))
        too_many = share(draft, HOME, ABOUT)
        settings.BUILDER_MAX_PREVIEW_LINKS = 1
        assert share(draft, HOME).status_code == status.HTTP_201_CREATED
        too_many_links = share(draft, HOME)

        assert too_many.json()["errors"][0]["code"] == "too_many_documents"
        assert (
            too_many_links.json()["errors"][0]["code"]
            == "too_many_preview_links"
        )

    def test_staff_who_only_look_may_list_but_not_share_or_revoke(
        self, draft, staff, catalogue, features_on
    ):
        link = share(draft, HOME).json()
        reader = api_as(staff)

        listed = reader.get(reverse("builder:preview-link-list"))
        made = share(reader, HOME)
        revoked = reader.delete(
            reverse("builder:preview-link-detail", kwargs={"pk": link["id"]})
        )

        assert listed.status_code == status.HTTP_200_OK
        assert made.status_code == status.HTTP_403_FORBIDDEN
        assert revoked.status_code == status.HTTP_403_FORBIDDEN
        assert BuilderPreviewLink.objects.count() == 1

    def test_a_shopper_and_a_visitor_may_not_manage_links(self, draft):
        from user.factories.account import UserAccountFactory

        for who in (api_as(UserAccountFactory()), APIClient()):
            for response in (
                who.get(reverse("builder:preview-link-list")),
                share(who, HOME),
            ):
                assert response.status_code in (
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                )
