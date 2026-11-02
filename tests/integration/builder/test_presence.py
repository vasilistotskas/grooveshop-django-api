"""``POST/DELETE builder/presence``: who else has a document open."""

from __future__ import annotations

import time

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.models import TenantMembershipRole
from tests.integration.builder.conftest import api_as
from tests.utils.staff import store_staff

pytestmark = pytest.mark.django_db

HOME = "template:home"
ABOUT = "page:about"


def beat(client, *documents):
    return client.post(
        reverse("builder:presence"),
        {"documents": list(documents)},
        format="json",
    )


def leave(client):
    return client.delete(reverse("builder:presence"))


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def maria(tenant):
    return store_staff(tenant, role=TenantMembershipRole.ADMIN)


@pytest.fixture
def nikos(tenant):
    return store_staff(tenant, role=TenantMembershipRole.OWNER)


class TestHeartbeat:
    def test_nobody_else_means_an_empty_list_per_document(self, maria):
        response = beat(api_as(maria), HOME, ABOUT)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"documents": {HOME: [], ABOUT: []}}

    def test_others_are_listed_and_the_caller_is_not(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME)

        seen_by_b = beat(b, HOME, ABOUT).json()["documents"]
        seen_by_a = beat(a, HOME).json()["documents"]

        (entry,) = seen_by_b[HOME]
        assert entry["id"] == maria.pk
        assert entry["name"] == (
            maria.get_full_name().strip() or maria.get_username()
        )
        assert entry["since"]
        assert seen_by_b[ABOUT] == []
        assert [e["id"] for e in seen_by_a[HOME]] == [nikos.pk]

    def test_since_survives_heartbeats(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME)
        first = beat(b, HOME).json()["documents"][HOME][0]["since"]

        beat(a, HOME)
        again = beat(b, HOME).json()["documents"][HOME][0]["since"]

        assert again == first

    def test_a_person_lapses_after_the_ttl(self, maria, nikos, settings):
        settings.BUILDER_PRESENCE_TTL_SECONDS = 1
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME)
        assert len(beat(b, HOME).json()["documents"][HOME]) == 1

        time.sleep(1.5)

        assert beat(b, HOME).json()["documents"][HOME] == []

    def test_documents_left_out_are_let_go_of(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME, ABOUT)

        beat(a, HOME)

        seen = beat(b, HOME, ABOUT).json()["documents"]
        assert [e["id"] for e in seen[HOME]] == [maria.pk]
        assert seen[ABOUT] == []

    def test_an_empty_heartbeat_leaves_everything(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME)

        beat(a)

        assert beat(b, HOME).json()["documents"][HOME] == []

    def test_a_repeated_key_counts_once(self, maria):
        response = beat(api_as(maria), HOME, HOME)

        assert list(response.json()["documents"]) == [HOME]


class TestSigningOut:
    def test_delete_removes_the_person_from_every_document(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME, ABOUT)

        response = leave(a)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        seen = beat(b, HOME, ABOUT).json()["documents"]
        assert seen == {HOME: [], ABOUT: []}

    def test_leaving_without_a_heartbeat_is_fine(self, maria):
        assert leave(api_as(maria)).status_code == status.HTTP_204_NO_CONTENT


class TestRefusing:
    def test_a_key_that_is_not_a_document_is_a_400(self, maria):
        response = beat(api_as(maria), HOME, "not a key")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error = response.json()["errors"][0]
        assert error["code"] == "invalid_key"
        assert error["path"] == "/documents/1"

    def test_too_many_documents_are_a_400(self, maria, settings):
        settings.BUILDER_PRESENCE_MAX_DOCUMENTS = 2

        assert (
            beat(api_as(maria), HOME, ABOUT).status_code == status.HTTP_200_OK
        )
        response = beat(api_as(maria), HOME, ABOUT, "page:contact")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["errors"][0]["code"] == "too_many_documents"

    def test_a_malformed_body_is_a_400(self, maria):
        response = api_as(maria).post(
            reverse("builder:presence"), {"documents": "home"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_failed_heartbeat_changes_nothing(self, maria, nikos):
        a, b = api_as(maria), api_as(nikos)
        beat(a, HOME)

        beat(a, ABOUT, "not a key")

        assert [e["id"] for e in beat(b, HOME).json()["documents"][HOME]] == [
            maria.pk
        ]


class TestWho:
    def test_staff_who_only_look_may_say_they_are_looking(self, maria, tenant):
        reader = store_staff(tenant, role=TenantMembershipRole.STAFF)
        beat(api_as(maria), HOME)

        response = beat(api_as(reader), HOME)

        assert response.status_code == status.HTTP_200_OK
        assert [e["id"] for e in response.json()["documents"][HOME]] == [
            maria.pk
        ]

    def test_a_shopper_and_a_visitor_may_not(self, tenant):
        from user.factories.account import UserAccountFactory

        for who in (api_as(UserAccountFactory()), APIClient()):
            for call in (beat, lambda client: leave(client)):
                response = call(who)

                assert response.status_code in (
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                )
