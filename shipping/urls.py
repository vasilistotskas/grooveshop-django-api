"""URL patterns for the generic shipping abstraction.

Mounted at ``api/v1/`` inside ``i18n_patterns`` in ``core/urls.py``.
Per-provider URLs live in their own apps (``shipping_acs.urls``,
``shipping_boxnow.urls``) and are mounted alongside this one.
"""

from __future__ import annotations

from django.urls import path

from shipping.views import ShippingOptionsView, ShippingProviderListView

urlpatterns = [
    path(
        "shipping/options",
        ShippingOptionsView.as_view(),
        name="shipping-options",
    ),
    path(
        "shipping/providers",
        ShippingProviderListView.as_view(),
        name="shipping-provider-list",
    ),
]
