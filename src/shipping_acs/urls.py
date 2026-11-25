"""URL patterns for the shipping_acs app.

The carrier's ``api_urlconf``, mounted at ``api/v1/`` after
``shipping.urls`` on a store's host. ACS has no webhook, so no
``webhook_urlconf``.
"""

from __future__ import annotations

from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from shipping_acs.views import (
    AcsAddressValidationView,
    AcsCancelView,
    AcsLabelView,
    AcsPickupListIssueView,
    AcsPickupListManifestView,
    AcsStationViewSet,
    AcsTrackingView,
)

urlpatterns = [
    # Stations / Smartpoints (Phase 2 read-only cache)
    path(
        "shipping/acs/stations",
        AcsStationViewSet.as_view({"get": "list"}),
        name="shipping-acs-station-list",
    ),
    path(
        "shipping/acs/stations/nearest",
        AcsStationViewSet.as_view({"get": "nearest"}),
        name="shipping-acs-station-nearest",
    ),
    # Detail is keyed by ``uuid`` — ``external_id`` is the AREA station
    # code shared by every locker in that area (50 lockers under 'ATH'),
    # so a code-keyed retrieve raised MultipleObjectsReturned → 500 the
    # moment the pair-keyed cache landed (2026-07-25).
    path(
        "shipping/acs/stations/<uuid:uuid>",
        AcsStationViewSet.as_view({"get": "retrieve"}),
        name="shipping-acs-station-detail",
    ),
    # Shipment endpoints
    path(
        "shipping/acs/shipments/<str:voucher_no>/label.pdf",
        AcsLabelView.as_view(),
        name="shipping-acs-label",
    ),
    path(
        "shipping/acs/shipments/<str:voucher_no>/cancel",
        AcsCancelView.as_view(),
        name="shipping-acs-cancel",
    ),
    path(
        "shipping/acs/shipments/<str:voucher_no>/tracking",
        AcsTrackingView.as_view(),
        name="shipping-acs-tracking",
    ),
    # Address validation (Phase 4b — public, cached)
    path(
        "shipping/acs/address-validation",
        AcsAddressValidationView.as_view(),
        name="shipping-acs-address-validation",
    ),
    # Pickup-list endpoints (admin-only)
    path(
        "shipping/acs/pickup-lists/issue",
        AcsPickupListIssueView.as_view(),
        name="shipping-acs-pickup-list-issue",
    ),
    path(
        "shipping/acs/pickup-lists/<str:pickup_list_no>/manifest.pdf",
        AcsPickupListManifestView.as_view(),
        name="shipping-acs-pickup-list-manifest",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
