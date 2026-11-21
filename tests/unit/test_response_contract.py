"""The guard that judges every API response against the schema
(``tests/response_contract.py``), on the real document."""

from __future__ import annotations

import pytest
from django.http import HttpResponse, JsonResponse

from tests import response_contract
from tests.response_contract import (
    CALL_FAILED,
    DRIFTS,
    Tally,
    check,
    drift_gone_report,
    failing_paths,
    operation_path,
    unmet_drifts,
)
from tests.utils.openapi import FORMATS, judge, openapi_document

PUBLIC_SETTINGS = "/api/v1/settings/public"


@pytest.fixture
def tally(monkeypatch):
    fresh = Tally()
    monkeypatch.setattr(response_contract, "TALLY", fresh)
    return fresh


def test_a_literal_route_wins_over_a_parametric_one():
    assert (
        operation_path("/api/v1/builder/media/resolve")
        == "/api/v1/builder/media/resolve"
    )
    assert operation_path("/api/v1/builder/media/7") == (
        "/api/v1/builder/media/{id}"
    )


def test_a_path_two_operations_fit_equally_fails():
    with pytest.raises(AssertionError, match="equally"):
        operation_path("/api/v1/product/alert/images")


def test_a_body_its_schema_rejects_fails_with_where_and_why(tally):
    with pytest.raises(AssertionError) as raised:
        check("GET", PUBLIC_SETTINGS, JsonResponse({"unexpected": 1}))

    message = str(raised.value)
    assert message.startswith(f"GET {PUBLIC_SETTINGS} answered 200 ")
    assert "at $" in message
    assert tally.validated == 1


def test_an_error_body_is_judged_too(tally):
    with pytest.raises(AssertionError, match="answered 500"):
        check("GET", PUBLIC_SETTINGS, JsonResponse({"detail": 1}, status=500))


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            JsonResponse({}, status=418),
            f"GET {PUBLIC_SETTINGS} 418: undocumented",
        ),
        (
            HttpResponse("a,b", content_type="text/csv"),
            f"GET {PUBLIC_SETTINGS} 200: no JSON body",
        ),
    ],
)
def test_what_cannot_be_judged_is_counted(tally, response, reason):
    check("GET", PUBLIC_SETTINGS, response)

    assert tally.not_judged == {reason: 1}
    assert tally.validated == 0


def test_a_path_outside_the_api_is_not_looked_at(tally):
    check("GET", "/admin/", HttpResponse("<html>"))

    assert tally == Tally()


def test_an_undocumented_status_under_a_strict_prefix_fails(tally):
    with pytest.raises(AssertionError, match="does not declare"):
        check(
            "GET",
            "/api/v1/builder/media/7",
            JsonResponse({}, status=418),
        )


HEALTH = "/api/v1/health"
#: A health body whose ``celery`` is not a boolean and has no ``redis``.
TWO_DRIFTS = {"database": True, "celery": "yes"}


def test_where_a_body_fails_is_named_by_path():
    validator = judge(openapi_document()).validator(
        openapi_document()["components"]["schemas"]["HealthCheckResponse"]
    )

    assert failing_paths(validator.iter_errors(TWO_DRIFTS)) == {
        "$.celery",
        "$.redis",
    }


@pytest.mark.known_schema_drift(
    "GET", HEALTH, 200, at=("$.celery", "$.redis"), issue=684
)
def test_a_marked_drift_is_let_through(tally):
    check("GET", HEALTH, JsonResponse(TWO_DRIFTS))

    assert DRIFTS.met == {("GET", HEALTH, 200)}


def test_a_failure_the_marker_does_not_name_still_fails(request, tally):
    request.applymarker(
        pytest.mark.known_schema_drift(
            "GET", HEALTH, 200, at="$.celery", issue=684
        )
    )

    with pytest.raises(
        AssertionError, match=r"it fails at \$\.celery, \$\.redis"
    ):
        check("GET", HEALTH, JsonResponse(TWO_DRIFTS))

    # Met by hand, so this test's own teardown does not report it.
    DRIFTS.met.add(("GET", HEALTH, 200))


def test_a_marker_applied_in_the_test_counts(request, tally):
    request.applymarker(
        pytest.mark.known_schema_drift(
            "GET", HEALTH, 200, at=("$.celery", "$.redis"), issue=684
        )
    )

    check("GET", HEALTH, JsonResponse(TWO_DRIFTS))

    assert DRIFTS.met == {("GET", HEALTH, 200)}


def test_a_marker_whose_drift_is_gone_is_reported(request):
    key = ("GET", HEALTH, 200)
    request.applymarker(pytest.mark.known_schema_drift(*key, at="$", issue=1))

    assert unmet_drifts() == [f"GET {HEALTH} 200 (issue 1)"]
    assert drift_gone_report() == (
        f"No body broke the schema for GET {HEALTH} 200 (issue 1): the "
        "drift is gone, remove its known_schema_drift marker"
    )
    # Met by hand, so this test's own teardown does not report it.
    DRIFTS.met.add(key)


def test_a_test_that_failed_is_not_told_its_drift_is_gone(request):
    request.applymarker(
        pytest.mark.known_schema_drift("GET", HEALTH, 200, at="$", issue=1)
    )
    request.node.stash[CALL_FAILED] = True

    assert drift_gone_report() is None

    request.node.stash[CALL_FAILED] = False
    DRIFTS.met.add(("GET", HEALTH, 200))


@pytest.mark.parametrize(
    ("value", "kind", "conforms"),
    [
        ("f16502d5-b2db-4af5-9b2a-f9bc3b2d87a8", "uuid", True),
        # Version 5 digit, variant 1: an 8-4-4-4-12 hex z.uuid() rejects.
        ("12345678-1234-5678-1234-567812345678", "uuid", False),
        ("a@b.gr", "email", True),
        ("user@localhost", "email", False),
        ("", "email", False),
        ("https://example.gr/a.png", "uri", True),
        ("http://testserver/api/v1/tag?page=2", "uri", True),
        ("mailto:a@b.gr", "uri", True),
        ("/media/a.png", "uri", False),
        ("http://[bad", "uri", False),
        ("http://example.gr:70000/", "uri", False),
        ("2026-11-21T10:00:00Z", "date-time", True),
        ("2026-11-21T10:00", "date-time", False),
        ("2026-11-21", "date", True),
        ("20261121", "date", False),
    ],
)
def test_a_format_is_what_the_storefront_accepts(value, kind, conforms):
    assert FORMATS.conforms(value, kind) is conforms


def test_a_literal_dot_in_a_route_is_not_a_wildcard():
    label = next(path for path in openapi_document()["paths"] if ".pdf" in path)

    assert operation_path(label) == label
    assert operation_path(label.replace(".pdf", "xpdf")) is None
