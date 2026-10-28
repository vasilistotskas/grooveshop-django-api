"""The boundary to the storefront: catalogue parsing, ETag revalidation,
and every way the storefront can fail to answer."""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from tests.utils.texts import catalogue_payload, entry, response
from texts import storefront as client


def test_the_catalogue_is_parsed_and_asked_for_the_stores_schema(stub):
    catalogue = client.fetch_catalogue("webside")

    assert catalogue.hash == "h1"
    assert catalogue.locales == ("el", "en")
    greeting = catalogue.get("components/Chrome/Footer", "greeting")
    assert greeting is not None
    assert greeting.defaults["en"] == "Hello {name}"
    assert greeting.placeholders["en"].params == ("named:name",)
    assert greeting.placeholders["en"].plural_cases == 1
    assert greeting.locked is False
    assert catalogue.get("components/Cookie/Control", "banner").locked is True
    assert catalogue.get("nope", "nope") is None
    assert stub.gets[0]["params"] == {"schema": "webside"}
    assert stub.gets[0]["url"] == ("http://storefront.test/api/texts/catalogue")


def test_a_fresh_copy_is_served_without_asking_again(stub):
    client.fetch_catalogue("webside")
    client.fetch_catalogue("webside")

    assert len(stub.gets) == 1


def test_a_stale_copy_is_revalidated_by_etag_and_kept_on_304(stub, settings):
    settings.TEXTS_CATALOGUE_REVALIDATE_SECONDS = 0
    first = client.fetch_catalogue("webside")
    second = client.fetch_catalogue("webside")

    assert second is first
    assert stub.gets[1]["headers"]["If-None-Match"] == '"h1"'


def test_a_new_build_replaces_the_copy(stub, settings):
    settings.TEXTS_CATALOGUE_REVALIDATE_SECONDS = 0
    client.fetch_catalogue("webside")
    stub.etag = '"h2"'
    stub.catalogue = catalogue_payload(
        [entry("components/X", "k", en="New")], build="h2"
    )

    catalogue = client.fetch_catalogue("webside")

    assert catalogue.hash == "h2"
    assert catalogue.get("components/X", "k") is not None


def test_each_schema_has_its_own_copy(stub):
    client.fetch_catalogue("webside")
    client.fetch_catalogue("demo")

    assert [g["params"]["schema"] for g in stub.gets] == [
        "webside",
        "demo",
    ]


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("down"),
        requests.Timeout("slow"),
    ],
)
def test_an_unreachable_fakeis_unavailable(stub, failure):
    with (
        mock.patch("texts.storefront.requests.get", side_effect=failure),
        pytest.raises(client.StorefrontUnavailable),
    ):
        client.fetch_catalogue("webside")


def test_an_error_status_is_unavailable(stub):
    with (
        mock.patch("texts.storefront.requests.get", return_value=response(502)),
        pytest.raises(client.StorefrontUnavailable),
    ):
        client.fetch_catalogue("webside")


def test_a_malformed_catalogue_is_unavailable(stub):
    with (
        mock.patch(
            "texts.storefront.requests.get",
            return_value=response(200, {"hash": "x"}),
        ),
        pytest.raises(client.StorefrontUnavailable),
    ):
        client.fetch_catalogue("webside")


def test_no_fakeurl_is_unavailable(stub, settings):
    settings.NUXT_INTERNAL_BASE_URL = ""

    with pytest.raises(client.StorefrontUnavailable):
        client.fetch_catalogue("webside")


def _validate(**overrides):
    args = {
        "namespace": "components/Chrome/Footer",
        "key": "tagline",
        "language": "en",
        "value": "Hi",
        "schema": "webside",
    }
    return client.validate_text(**{**args, **overrides})


def test_validation_sends_the_text_and_the_schema(stub):
    assert _validate() is None

    assert stub.posts[0]["json"] == {
        "namespace": "components/Chrome/Footer",
        "key": "tagline",
        "locale": "en",
        "value": "Hi",
        "schema": "webside",
    }
    assert stub.posts[0]["url"] == ("http://storefront.test/api/texts/validate")


@pytest.mark.parametrize(
    "code",
    [
        client.UNKNOWN_KEY,
        client.LOCKED_KEY,
        client.SYNTAX_ERROR,
        client.PLACEHOLDER_MISMATCH,
    ],
)
def test_every_rejection_code_is_carried_through(stub, code):
    stub.verdict = lambda *a: (code, "why")

    rejection = _validate()

    assert rejection == client.Rejection(code=code, detail="why")


@pytest.mark.parametrize(
    "reply",
    [
        response(500),
        response(404),
        response(422, {"ok": False, "code": "something_new", "detail": ""}),
        response(422, ["not", "a", "dict"]),
    ],
)
def test_any_other_answer_is_unavailable_never_accepted(stub, reply):
    with (
        mock.patch("texts.storefront.requests.post", return_value=reply),
        pytest.raises(client.StorefrontUnavailable),
    ):
        _validate()


def test_validation_with_the_fakedown_is_unavailable(stub):
    stub.down = True

    with pytest.raises(client.StorefrontUnavailable):
        _validate()
