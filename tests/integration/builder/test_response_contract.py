"""The builder's answers satisfy the schema the storefront generates from.

The storefront refuses (422) any body its generated Zod rejects, so a field
that is ``null`` on the wire must be ``nullable`` in ``schema.yml``: a normal
release has no ``rolledBackFrom``, a pending schedule has no ``release``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from builder import scheduling, services
from builder.models import (
    BuilderDocument,
    BuilderScheduledPublish,
    ScheduleStatus,
)
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero
from tests.utils.openapi import assert_matches_response

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("catalogue", "features_on"),
]

HOME = "template:home"


@pytest.fixture
def client(admin):
    services.save_draft(
        admin,
        key=HOME,
        draft=document(("a", hero("one"))),
        i18n={},
        expected_revision=0,
    )
    return api_as(admin)


def change(admin, title: str) -> None:
    """Edit the draft, so the next publish has something to release."""
    revision = BuilderDocument.objects.get(key=HOME).draft_revision
    services.save_draft(
        admin,
        key=HOME,
        draft=document(("a", hero(title))),
        i18n={},
        expected_revision=revision,
    )


def path_of(name: str, **kwargs) -> str:
    return reverse(name, kwargs=kwargs)


class TestReleases:
    def test_a_normal_release_and_a_rollback_match_the_schema(
        self, admin, client, openapi_schema
    ):
        published = client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )
        change(admin, "two")
        client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )
        rolled = client.post(
            reverse("builder:rollback"),
            {"release": published.json()["id"]},
            format="json",
        )
        listed = client.get(reverse("builder:release-list"))

        assert published.status_code == status.HTTP_201_CREATED
        assert published.json()["rolledBackFrom"] is None
        assert_matches_response(
            openapi_schema,
            path_of("builder:publish"),
            published.json(),
            method="post",
            status="201",
        )
        assert_matches_response(
            openapi_schema,
            path_of("builder:rollback"),
            rolled.json(),
            method="post",
            status=str(rolled.status_code),
        )
        assert rolled.json()["rolledBackFrom"] == published.json()["number"]
        assert_matches_response(
            openapi_schema, path_of("builder:release-list"), listed.json()
        )


class TestSchedules:
    def test_pending_and_published_schedules_match_the_schema(
        self, admin, client, openapi_schema
    ):
        pending = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=2)
        )
        BuilderScheduledPublish.objects.filter(pk=pending.pk).update(
            scheduled_for=timezone.now() - timedelta(minutes=1)
        )
        scheduling.run_due()
        change(admin, "two")
        later = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=3)
        )

        listed = client.get(reverse("builder:schedule-list"))

        rows = {r["id"]: r for r in listed.json()["results"]}
        assert rows[pending.pk]["release"] is not None
        assert rows[later.pk]["release"] is None
        assert_matches_response(
            openapi_schema, path_of("builder:schedule-list"), listed.json()
        )

    @pytest.mark.parametrize(
        "wanted", [ScheduleStatus.PENDING, ScheduleStatus.PUBLISHED]
    )
    def test_status_narrows_the_list_and_is_declared(
        self, admin, client, openapi_schema, wanted
    ):
        done = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=2)
        )
        BuilderScheduledPublish.objects.filter(pk=done.pk).update(
            scheduled_for=timezone.now() - timedelta(minutes=1)
        )
        scheduling.run_due()
        change(admin, "two")
        waiting = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=3)
        )

        listed = client.get(
            reverse("builder:schedule-list"), {"status": wanted.value}
        )

        assert [r["id"] for r in listed.json()["results"]] == [
            (waiting if wanted is ScheduleStatus.PENDING else done).pk
        ]
        parameters = openapi_schema["paths"][path_of("builder:schedule-list")][
            "get"
        ]["parameters"]
        declared = next(p for p in parameters if p["name"] == "status")
        assert set(declared["schema"]["enum"]) == set(ScheduleStatus.values)
