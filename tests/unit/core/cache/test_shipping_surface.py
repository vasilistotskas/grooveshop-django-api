"""The ``shipping`` surface owns the cached shipping reads.

``GET /api/v1/shipping/policy`` is cached by ``cache_methods`` and
answers from the same inputs as checkout, so the surface must purge it
and purge itself when any of them changes.
"""

from __future__ import annotations

from core.cache.registry import get_surface
from core.utils.views import response_cache_key_prefix
from shipping.cache_surfaces import PRODUCT_SHIPPING_HANDLER
from shipping.views import ProductShippingView, ShippingPolicyView


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


def test_a_product_or_promotion_edit_purges_the_product_shipping_price():
    """``GET /shipping/product/<id>`` moves with a rate, with the
    product (price, weight, size) and with a promotion, so each of the
    three surfaces those edits mark stale purges it, Django and Nuxt."""
    key = response_cache_key_prefix(ProductShippingView.__name__, "get")

    for code, model in (
        ("shipping", "shipping.ShippingRate"),
        ("products", "product.Product"),
        ("promotions", "promotion.Promotion"),
    ):
        surface = get_surface(code)
        assert model in surface.invalidated_by
        assert any(
            pattern.strip("*") in key and pattern.startswith("*")
            for pattern in surface.django_patterns
        ), code
        assert any(
            PRODUCT_SHIPPING_HANDLER in pattern
            for pattern in surface.nuxt_patterns
        ), code
