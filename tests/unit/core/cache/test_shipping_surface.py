"""The ``shipping`` surface owns the cached shipping policy.

``GET /api/v1/shipping/policy`` is cached by ``cache_methods`` and
answers from the same inputs as checkout, so the surface must purge it
and purge itself when any of them changes.
"""

from __future__ import annotations

from core.cache.registry import get_surface
from core.utils.views import response_cache_key_prefix
from shipping.views import ShippingPolicyView


def test_the_surface_purges_the_cached_policy_response():
    key = response_cache_key_prefix(ShippingPolicyView.__name__, "get")
    patterns = get_surface("shipping").django_patterns

    assert any(
        pattern.strip("*") in key and pattern.startswith("*")
        for pattern in patterns
    )


def test_the_surface_is_stale_when_a_policy_input_changes():
    invalidated_by = get_surface("shipping").invalidated_by

    assert {
        "shipping.ShippingRate",
        "shipping.ShippingProvider",
        "shipping_acs.AcsStation",
    } <= set(invalidated_by)
