"""The API error contract: today's body, plus ``code`` and ``fields``.

Each case runs a real view through the configured ``EXCEPTION_HANDLER`` and
the camelCase renderer, so what is asserted is what a client receives.
"""

from __future__ import annotations

import json

import pytest
from django.http import Http404
from django.utils import translation
from rest_framework import serializers, status
from rest_framework.exceptions import (
    APIException,
    NotAuthenticated,
    NotFound,
    Throttled,
    ValidationError,
)
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.api.exception_handler import INVALID_CODE, exception_handler


class _Form(serializers.Serializer):
    first_name = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        if attrs["first_name"] == "nobody":
            raise serializers.ValidationError(
                "Pick a real name.", code="name_taken"
            )
        return attrs


class _Unavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Try again later."
    default_code = "payments_down"


def _call(raiser, data=None):
    class View(APIView):
        permission_classes = [AllowAny]
        authentication_classes = []
        throttle_classes = []

        def post(self, request):
            raiser(request)

    request = APIRequestFactory().post("/x", data or {}, format="json")
    with translation.override("en"):
        response = View.as_view()(request)
        response.render()
    return response, json.loads(response.content)


def _validate(request):
    _Form(data=request.data).is_valid(raise_exception=True)


def test_a_field_error_keeps_its_body_and_gains_code_and_fields():
    response, body = _call(_validate, {"first_name": "", "quantity": 0})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert body["firstName"] == ["This field may not be blank."]
    assert body["quantity"] == [
        "Ensure this value is greater than or equal to 1."
    ]
    assert body["code"] == INVALID_CODE
    assert body["fields"] == {
        "firstName": [
            {"message": "This field may not be blank.", "code": "blank"}
        ],
        "quantity": [
            {
                "message": "Ensure this value is greater than or equal to 1.",
                "code": "min_value",
            }
        ],
    }


def test_one_shared_code_is_the_top_level_code():
    _, body = _call(_validate, {"quantity": 1})

    assert body["code"] == "required"
    assert body["fields"] == {
        "firstName": [
            {"message": "This field is required.", "code": "required"}
        ]
    }


def test_a_non_field_error_lands_under_non_field_errors():
    _, body = _call(_validate, {"first_name": "nobody", "quantity": 1})

    assert body["nonFieldErrors"] == ["Pick a real name."]
    assert body["code"] == "name_taken"
    assert body["fields"] == {
        "nonFieldErrors": [
            {"message": "Pick a real name.", "code": "name_taken"}
        ]
    }


def test_a_string_value_still_reads_as_a_list_in_fields():
    def raiser(request):
        raise ValidationError({"product": "Buy it first."}, code="must_buy")

    _, body = _call(raiser)

    assert body["product"] == "Buy it first."
    assert body["code"] == "must_buy"
    assert body["fields"] == {
        "product": [{"message": "Buy it first.", "code": "must_buy"}]
    }


def test_nested_serializer_errors_keep_their_nesting():
    class Line(serializers.Serializer):
        quantity = serializers.IntegerField(min_value=1)

    class Cart(serializers.Serializer):
        items = Line(many=True)

    def raiser(request):
        Cart(data=request.data).is_valid(raise_exception=True)

    _, body = _call(raiser, {"items": [{"quantity": 2}, {"quantity": 0}]})

    assert body["fields"]["items"]["1"]["quantity"] == [
        {
            "message": "Ensure this value is greater than or equal to 1.",
            "code": "min_value",
        }
    ]


def test_a_validation_error_with_only_a_detail_key_still_gets_fields():
    def raiser(request):
        raise ValidationError({"detail": "Cannot do that."}, code="blocked")

    _, body = _call(raiser)

    assert body["detail"] == "Cannot do that."
    assert body["code"] == "blocked"
    assert body["fields"] == {
        "detail": [{"message": "Cannot do that.", "code": "blocked"}]
    }


def test_a_field_named_code_is_never_overwritten():
    def raiser(request):
        raise ValidationError(
            {"code": "Unknown coupon."}, code="coupon_unknown"
        )

    _, body = _call(raiser)

    assert body["code"] == "Unknown coupon."
    assert body["fields"]["code"] == [
        {"message": "Unknown coupon.", "code": "coupon_unknown"}
    ]


def test_a_plain_error_gains_code_and_nothing_else():
    def raiser(request):
        raise NotAuthenticated()

    response, body = _call(raiser)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert body == {
        "detail": "Authentication credentials were not provided.",
        "code": "not_authenticated",
    }


@pytest.mark.parametrize("exc", [NotFound, Http404])
def test_a_404_is_detail_and_not_found(exc):
    def raiser(request):
        raise exc()

    response, body = _call(raiser)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert body == {"detail": "Not found.", "code": "not_found"}


def test_a_5xx_gains_code_only():
    def raiser(request):
        raise _Unavailable()

    response, body = _call(raiser)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert body == {"detail": "Try again later.", "code": "payments_down"}


def test_an_unexpected_error_is_left_to_django_and_carries_nothing():
    secret = RuntimeError("password=hunter2")

    assert exception_handler(secret, {}) is None


def test_response_headers_survive():
    def raiser(request):
        raise Throttled(wait=7)

    response, body = _call(raiser)

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response["Retry-After"] == "7"
    assert body["code"] == "throttled"


def test_a_bare_list_body_is_left_as_it_was():
    def raiser(request):
        raise ValidationError("Nope.")

    response, body = _call(raiser)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert body == ["Nope."]
