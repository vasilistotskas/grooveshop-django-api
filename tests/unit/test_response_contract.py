"""The guard that judges every API response against the schema
(``tests/response_contract.py``), on the real document."""

from __future__ import annotations

import pytest
from django.http import HttpResponse, JsonResponse

from tests import response_contract
from tests.response_contract import (
    DRIFTS,
    Tally,
    check,
    operation_path,
    unmet_drifts,
)

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


@pytest.mark.known_schema_drift("GET", PUBLIC_SETTINGS, 200, issue=684)
def test_a_marked_drift_is_let_through(tally):
    check("GET", PUBLIC_SETTINGS, JsonResponse({"unexpected": 1}))

    assert DRIFTS.met == {("GET", PUBLIC_SETTINGS, 200)}


def test_a_marker_applied_in_the_test_counts(request, tally):
    request.applymarker(
        pytest.mark.known_schema_drift("GET", PUBLIC_SETTINGS, 200, issue=684)
    )

    check("GET", PUBLIC_SETTINGS, JsonResponse({"unexpected": 1}))

    assert DRIFTS.met == {("GET", PUBLIC_SETTINGS, 200)}


def test_a_marker_whose_drift_is_gone_is_reported(request):
    key = ("GET", PUBLIC_SETTINGS, 200)
    request.applymarker(pytest.mark.known_schema_drift(*key, issue=1))

    assert unmet_drifts() == [f"GET {PUBLIC_SETTINGS} 200 (issue 1)"]
    # Met by hand, so this test's own teardown does not report it.
    DRIFTS.met.add(key)
