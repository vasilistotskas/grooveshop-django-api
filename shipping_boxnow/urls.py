"""URL patterns for the shipping_boxnow app.

Mounted at ``api/v1/`` inside ``i18n_patterns`` in ``core/urls.py``.
The webhook URL lives at the **root** level (not under api/v1/) so that
BoxNow can reach it without authentication headers or language prefixes.
"""

from __future__ import annotations

from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from shipping_boxnow.views.locker import BoxNowLockerViewSet
from shipping_boxnow.views.shipment import (
    BoxNowCancelView,
    BoxNowLabelView,
)

urlpatterns = [
    # ------------------------------------------------------------------ #
    # Locker endpoints                                                     #
    # ------------------------------------------------------------------ #
    path(
        "shipping/boxnow/lockers",
        BoxNowLockerViewSet.as_view({"get": "list"}),
        name="shipping-boxnow-locker-list",
    ),
    path(
        "shipping/boxnow/lockers/nearest",
        BoxNowLockerViewSet.as_view({"post": "nearest"}),
        name="shipping-boxnow-locker-nearest",
    ),
    path(
        "shipping/boxnow/lockers/<str:pk>",
        BoxNowLockerViewSet.as_view({"get": "retrieve"}),
        name="shipping-boxnow-locker-detail",
    ),
    # ------------------------------------------------------------------ #
    # Parcel / shipment endpoints                                         #
    # ------------------------------------------------------------------ #
    path(
        "shipping/boxnow/parcels/<str:parcel_id>/label.pdf",
        BoxNowLabelView.as_view(),
        name="shipping-boxnow-label",
    ),
    path(
        "shipping/boxnow/parcels/<str:parcel_id>/cancel",
        BoxNowCancelView.as_view(),
        name="shipping-boxnow-cancel",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
