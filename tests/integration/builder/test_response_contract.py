"""The builder's answers satisfy the schema the storefront generates from.

The storefront refuses (422) any body its generated Zod rejects, so a field
that is ``null`` on the wire must be ``nullable`` in ``schema.yml``: a normal
release has no ``rolledBackFrom``, a pending schedule has no ``release``.
"""

from __future__ import annotations

from datetime import timedelta

import jsonschema
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from builder import scheduling, services
from builder.errors import Issue
from builder.models import (
    BuilderDocument,
    BuilderScheduledPublish,
    ScheduleStatus,
)
from builder.serializers import PublishProblemSerializer
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero
from tests.utils.openapi import as_json_schema, assert_matches_response

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


class TestProblems:
    """A problem always has a ``document`` key: the schema requires it."""

    def publish_once(self, client):
        client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

    def test_a_check_with_nothing_to_publish_matches_the_schema(
        self, client, openapi_schema
    ):
        self.publish_once(client)

        checked = client.post(
            reverse("builder:publish-check"),
            {"documents": [HOME]},
            format="json",
        )

        problems = checked.json()["problems"]
        assert [p["code"] for p in problems] == ["nothing_to_publish"]
        assert "document" in problems[0]
        assert problems[0]["document"] is None
        assert_matches_response(
            openapi_schema,
            path_of("builder:publish-check"),
            checked.json(),
            method="post",
        )

    def test_the_publish_400_matches_the_schema(self, client, openapi_schema):
        self.publish_once(client)

        refused = client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert refused.json()["code"] == "invalid_document"
        assert refused.json()["errors"][0]["document"] is None
        # The 400 is documented as a plain error; the problems it lists are
        # the check's problems, and every one satisfies their schema.
        problem = openapi_schema["components"]["schemas"]["PublishProblem"]
        for item in refused.json()["errors"]:
            jsonschema.Draft4Validator(
                as_json_schema(problem, openapi_schema["components"]["schemas"])
            ).validate(item)

    @pytest.mark.parametrize(
        "issue",
        [
            Issue(path="/a", code="c", message="m"),
            Issue(path="/a", code="c", message="m", document=HOME),
            Issue(
                path="/a",
                code="c",
                message="m",
                document=HOME,
                params={"kind": "product"},
            ),
        ],
    )
    def test_an_issue_is_what_the_serializer_declares(self, issue):
        assert PublishProblemSerializer(issue.as_dict()).data == issue.as_dict()
