"""URL patterns for the generic shipping abstraction.

Mounted at ``api/v1/`` inside ``i18n_patterns`` in ``src/core/urls.py``,
followed by each registered carrier's own (its ``api_urlconf``).
"""

from __future__ import annotations

from django.urls import include, path

from shipping.interfaces import registered_providers
from shipping.views import (
    FreeShippingInfoView,
    ProductShippingView,
    ShippingOptionsView,
    ShippingPolicyView,
    ShippingProviderListView,
)

urlpatterns = [
    path(
        "shipping/options",
        ShippingOptionsView.as_view(),
        name="shipping-options",
    ),
    path(
        "shipping/free-shipping-info",
        FreeShippingInfoView.as_view(),
        name="shipping-free-shipping-info",
    ),
    path(
        "shipping/policy",
        ShippingPolicyView.as_view(),
        name="shipping-policy",
    ),
    path(
        "shipping/product/<int:product_id>",
        ProductShippingView.as_view(),
        name="shipping-product",
    ),
    path(
        "shipping/providers",
        ShippingProviderListView.as_view(),
        name="shipping-provider-list",
    ),
    *(
        path("", include(carrier.api_urlconf))
        for carrier in registered_providers()
        if carrier.api_urlconf is not None
    ),
]
