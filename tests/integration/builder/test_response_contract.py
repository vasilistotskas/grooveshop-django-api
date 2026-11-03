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


class TestInvalidDocumentIsTyped:
    """A refusal that lists problems is the third branch of the 400, so the
    storefront needs no schema of its own for it."""

    BRANCH = "BuilderDocumentInvalidError"

    def refusal_matches(self, openapi, path, method, body):
        branches = [
            ref["$ref"].rsplit("/", 1)[-1]
            for ref in openapi["paths"][path][method]["responses"]["400"][
                "content"
            ]["application/json"]["schema"]["anyOf"]
        ]
        assert branches == ["ApiValidationError", "ApiError", self.BRANCH]
        schemas = openapi["components"]["schemas"]
        jsonschema.Draft4Validator(
            as_json_schema(schemas[self.BRANCH], schemas)
        ).validate(body)
        assert body["code"] == "invalid_document"
        assert body["errors"]

    def test_a_draft_save(self, client, openapi_schema):
        path = path_of("builder:document-draft", key=HOME)

        refused = client.put(
            path,
            {"draft": {"sections": "nope"}, "i18n": {}},
            format="json",
            headers={"If-Match": "99"},
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        self.refusal_matches(
            openapi_schema,
            "/api/v1/builder/documents/{key}/draft",
            "put",
            refused.json(),
        )

    def test_a_publish(self, client, openapi_schema):
        client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

        refused = client.post(
            reverse("builder:publish"), {"documents": [HOME]}, format="json"
        )

        self.refusal_matches(
            openapi_schema,
            path_of("builder:publish"),
            "post",
            refused.json(),
        )

    def test_a_schedule(self, client, openapi_schema):
        refused = client.post(
            reverse("builder:schedule-list"),
            {
                "documents": [HOME],
                "scheduledFor": (
                    timezone.now() - timedelta(days=1)
                ).isoformat(),
            },
            format="json",
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        self.refusal_matches(
            openapi_schema,
            path_of("builder:schedule-list"),
            "post",
            refused.json(),
        )

    def test_every_operation_that_can_refuse_names_the_branch(
        self, openapi_schema
    ):
        named = {
            (path, method)
            for path, operations in openapi_schema["paths"].items()
            for method, operation in operations.items()
            if method != "parameters"
            and self.BRANCH
            in str(operation.get("responses", {}).get("400", ""))
        }

        assert len(named) == 11


class TestAFailedScheduleListsTypedProblems:
    def test_failure_matches_the_schema(self, admin, client, openapi_schema):
        failed = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=2)
        )
        later = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=3)
        )
        BuilderScheduledPublish.objects.filter(pk=failed.pk).update(
            status=ScheduleStatus.FAILED,
            failure=[
                Issue(
                    path="/a",
                    code="reference_not_found",
                    message="m",
                    document=HOME,
                    params={"kind": "product", "target": "1"},
                ).as_dict()
            ],
        )
        # A failure stored before ``document`` was always written.
        BuilderScheduledPublish.objects.filter(pk=later.pk).update(
            status=ScheduleStatus.FAILED,
            failure=[{"path": "/a", "code": "c", "message": "m"}],
        )

        listed = client.get(reverse("builder:schedule-list"))

        rows = {r["id"]: r for r in listed.json()["results"]}
        assert rows[failed.pk]["failure"][0]["document"] == HOME
        assert rows[later.pk]["failure"][0]["document"] is None
        assert_matches_response(
            openapi_schema, path_of("builder:schedule-list"), listed.json()
        )


class TestEveryErrorStatusIsTyped:
    """The statuses the builder answers are declared, each with its body, so
    the storefront needs no schema of its own for a refusal. (Every answer a
    test in this directory provokes is also checked against its operation by
    ``every_answer_is_in_the_schema``.)"""

    def test_a_stale_revision_names_the_current_one_and_who_moved_it(
        self, client, openapi_schema
    ):
        draft = path_of("builder:document-draft", key=HOME)

        stale = client.put(
            draft,
            {"draft": document(("a", hero("two"))), "i18n": {}},
            format="json",
            headers={"If-Match": "99"},
        )

        assert stale.status_code == status.HTTP_409_CONFLICT
        body = stale.json()
        assert body["code"] == "revision_conflict"
        assert isinstance(body["currentRevision"], int)
        assert "updatedBy" in body
        assert body["updatedAt"]

    def test_a_change_without_if_match_is_a_428(self, client):
        refused = client.put(
            path_of("builder:document-draft", key=HOME),
            {"draft": document(("a", hero("two"))), "i18n": {}},
            format="json",
        )

        assert refused.status_code == status.HTTP_428_PRECONDITION_REQUIRED
        assert refused.json()["code"] == "precondition_required"

    def test_an_unknown_document_is_a_404(self, client):
        missing = client.get(
            path_of("builder:document-detail", key="page:nowhere")
        )

        assert missing.status_code == status.HTTP_404_NOT_FOUND
        assert missing.json()["code"] == "not_found"

    def test_a_schedule_that_is_no_longer_pending_is_a_409(self, admin, client):
        schedule = scheduling.schedule_publish(
            admin, keys=[HOME], when=timezone.now() + timedelta(hours=2)
        )
        url = path_of("builder:schedule-detail", pk=schedule.pk)
        client.delete(url)

        again = client.delete(url)

        assert again.status_code == status.HTTP_409_CONFLICT
        assert again.json()["code"] == "schedule_not_pending"

    def test_every_operation_with_if_match_declares_both_refusals(
        self, openapi_schema
    ):
        for path, operations in openapi_schema["paths"].items():
            for method, operation in operations.items():
                if method == "parameters":
                    continue
                if not any(
                    p.get("name") == "If-Match"
                    for p in operation.get("parameters", [])
                ):
                    continue
                responses = operation["responses"]
                conflict = responses["409"]["content"]["application/json"][
                    "schema"
                ]
                assert conflict == {
                    "$ref": "#/components/schemas/BuilderRevisionConflict"
                }, (method, path)
                assert "428" in responses, (method, path)
