"""A scoped throttle must not stop existing when the caller signs in.

`AnonRateThrottle.get_cache_key` returns `None` for an authenticated
request — that is its documented job, and it is the right base for the
`*AnonThrottle` classes, each of which has a `UserRateThrottle` sibling
covering the other half of the traffic.

It is the wrong base for a budget meant to bound an *endpoint*. Built on
it, "this endpoint must not be enumerable" (gift-card codes), "a
brute-forceable code oracle" (coupons) and "a request amplifier against
both our workers and VIES" (B2B submit) applied to visitors only.

Five of those endpoints carried no other throttle at all — B2B submit,
product view-count, the Viva return resolver, and the ACS and BoxNow
partner-API proxies — so signing in was how you removed the limit.
`B2BProfileSubmitThrottle` was the starkest: its action is
`IsAuthenticated`, so the throttle could never fire for anybody.
"""

from __future__ import annotations

import inspect

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from core.api import throttling
from core.api.throttling import UserOrIpRateThrottle

User = get_user_model()


def _scoped_throttle_classes():
    """Every throttle this module defines that carries its own scope."""
    return [
        obj
        for _, obj in inspect.getmembers(throttling, inspect.isclass)
        if obj.__module__ == throttling.__name__
        and getattr(obj, "scope", None)
        and obj is not UserOrIpRateThrottle
    ]


def _request(user=None):
    request = APIRequestFactory().post("/api/v1/whatever")
    request.user = user
    # DRF's throttles read `request.user`; APIRequestFactory returns a
    # plain HttpRequest, so an unauthenticated caller is spelled with
    # AnonymousUser rather than None.
    if user is None:
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
    return request


@pytest.mark.django_db
def test_no_scoped_budget_disappears_for_a_signed_in_caller():
    signed_in = User.objects.create_user(
        email="caller@example.gr", password="pw", username="caller"
    )

    inert = []
    for klass in _scoped_throttle_classes():
        if "Anon" in klass.__name__:
            # Deliberately anon-only: each has a UserRateThrottle
            # sibling on the same scope pair.
            continue
        if klass.get_cache_key(klass(), _request(signed_in), None) is None:
            inert.append(f"{klass.__name__} (scope={klass.scope})")

    assert not inert, (
        "These scoped throttles return no cache key for an authenticated "
        "request, so their budget applies to visitors only and signing "
        "in removes it:\n  " + "\n  ".join(sorted(inert))
    )


@pytest.mark.django_db
def test_a_signed_in_caller_gets_their_own_bucket_not_a_shared_one():
    """Keying by user id, not by IP, so one caller cannot spend another's."""
    one = User.objects.create_user(
        email="one@example.gr", password="pw", username="one"
    )
    two = User.objects.create_user(
        email="two@example.gr", password="pw", username="two"
    )
    throttle = throttling.GiftCardCheckThrottle()

    assert throttle.get_cache_key(
        _request(one), None
    ) != throttle.get_cache_key(_request(two), None)


def test_anonymous_callers_are_still_keyed_by_address():
    throttle = throttling.GiftCardCheckThrottle()

    key = throttle.get_cache_key(_request(), None)

    assert key is not None
    assert "user:" not in key


@pytest.mark.django_db
def test_the_anon_half_of_a_pair_stays_anon_only():
    """The Anon/User pairs are a deliberate design and must not drift."""
    signed_in = User.objects.create_user(
        email="pair@example.gr", password="pw", username="pair"
    )

    for name in (
        "PaymentAttemptAnonThrottle",
        "OrderCreateAnonThrottle",
        "CartMutationAnonThrottle",
    ):
        klass = getattr(throttling, name)
        assert klass().get_cache_key(_request(signed_in), None) is None, (
            f"{name} is half of an Anon/User pair — its sibling covers "
            f"authenticated callers, so it must stay anon-only."
        )
