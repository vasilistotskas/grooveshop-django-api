"""Tests for the ``ShippingProvider.logo`` field and its API exposure.

Pins the contract that:
* The model field accepts an upload and the helper properties return
  the expected relative path + filename.
* The shipping-options endpoint (which the storefront actually reads)
  surfaces ``logoUrl`` for uploaded logos and ``null`` for providers
  that haven't been customised yet — the storefront's bundled
  fallback handles the latter case so a fresh deploy still renders.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from shipping.models import ShippingProvider

pytestmark = pytest.mark.django_db


def _make_png_upload(name: str = "logo.png") -> SimpleUploadedFile:
    """Return a tiny in-memory PNG suitable for ImageField validation."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(0, 0, 0)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


# Mock ``Setting.get`` instead of writing rows via ``update_or_create``
# — the latter races the ``_reseed_extra_settings`` autouse fixture
# under xdist parallel workers, leading to flaky reads. See
# ``project_settings_update_or_create_flake.md`` and the matching
# pattern in ``test_free_shipping_info.py`` / ``test_create_payment_
# intent_shipping.py``.
def _setting_get_with_smartpoint_enabled(name: str, default=None):
    if name == "ACS_SMARTPOINT_ENABLED":
        return True
    return default


def test_logo_defaults_to_blank():
    provider = ShippingProvider.objects.get(code="acs")
    # ImageField with blank=True, null=True yields a falsy file object
    # for fresh rows / rows where the operator hasn't uploaded yet.
    assert not provider.logo
    assert provider.main_image_path == ""
    assert provider.logo_filename == ""


def test_main_image_path_reflects_upload():
    provider = ShippingProvider.objects.get(code="boxnow")
    provider.logo = _make_png_upload("boxnow-test.png")
    provider.save(update_fields=["logo"])

    assert provider.logo_filename.endswith(".png")
    assert provider.main_image_path.startswith("media/public/uploads/shipping/")
    assert provider.main_image_path.endswith(provider.logo_filename)


def test_logo_for_kind_no_uploads_returns_empty_primary():
    """No uploads at all → both kinds return the empty primary
    ``logo`` field (which downstream treats as "no logo, use bundled
    fallback"). The model never throws or returns None directly —
    it returns the (possibly empty) FieldFile to keep the
    ``.url`` access pattern uniform.
    """
    provider = ShippingProvider.objects.get(code="acs")
    home = provider.logo_for_kind("home_delivery")
    pickup = provider.logo_for_kind("pickup_point")
    # Both should be the same empty primary field.
    assert not home
    assert not pickup
    assert provider.logo_url_for_kind("home_delivery") is None
    assert provider.logo_url_for_kind("pickup_point") is None


def test_logo_for_kind_primary_only_shared_across_kinds():
    """Only ``logo`` uploaded → both kinds resolve to it. Pins the
    backward-compat behaviour for carriers that haven't bothered to
    set the pickup-specific variant.
    """
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-primary.png")
    provider.save(update_fields=["logo"])

    assert (
        provider.logo_for_kind("home_delivery").name
        == provider.logo_for_kind("pickup_point").name
        == provider.logo.name
    )
    assert provider.logo_url_for_kind(
        "home_delivery"
    ) == provider.logo_url_for_kind("pickup_point")


def test_logo_for_kind_pickup_specific_takes_precedence():
    """Both fields uploaded → home delivery gets the primary, pickup
    gets the pickup-specific one. The case the user reported (ACS
    home vs Smartpoint should look different).
    """
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-home.png")
    provider.logo_pickup_point = _make_png_upload("acs-locker.png")
    provider.save(update_fields=["logo", "logo_pickup_point"])

    assert (
        provider.logo_for_kind("home_delivery").name
        != provider.logo_for_kind("pickup_point").name
    )
    assert provider.logo_for_kind("home_delivery").name == provider.logo.name
    assert (
        provider.logo_for_kind("pickup_point").name
        == provider.logo_pickup_point.name
    )


def test_logo_for_kind_unknown_kind_falls_back_to_primary():
    """Defensive: a hypothetical future ``ShippingKind`` value that
    isn't ``pickup_point`` falls through to ``logo`` instead of
    crashing. Keeps the helper safe to call from generic code that
    iterates ``ShippingKind.choices`` without coupling to specific
    members.
    """
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-primary.png")
    provider.save(update_fields=["logo"])

    field = provider.logo_for_kind("express_delivery")
    assert field.name == provider.logo.name


@patch(
    "extra_settings.models.Setting.get",
    side_effect=_setting_get_with_smartpoint_enabled,
)
def test_options_endpoint_returns_null_logo_when_no_upload(_mock_setting):
    ShippingProvider.objects.filter(code="acs").update(is_active=True)

    client = APIClient()
    url = reverse("shipping-options")
    response = client.get(url, {"orderValueAmount": "20", "currency": "EUR"})
    assert response.status_code == 200

    body = response.json()
    acs_rows = [row for row in body if row["providerCode"] == "acs"]
    assert acs_rows, "ACS rows missing from options response"
    for row in acs_rows:
        assert row["logoUrl"] is None


@patch(
    "extra_settings.models.Setting.get",
    side_effect=_setting_get_with_smartpoint_enabled,
)
def test_options_endpoint_pickup_kind_uses_pickup_logo_when_set(_mock_setting):
    """When ``logo_pickup_point`` is uploaded, the pickup_point
    option row carries IT as logoUrl while the home_delivery row
    keeps the primary ``logo``. Same ACS carrier, different image
    per kind — the case the user reported (ACS home delivery vs ACS
    Smartpoint should look different in the picker).
    """
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-home.png")
    provider.logo_pickup_point = _make_png_upload("acs-smartpoint.png")
    provider.save(update_fields=["logo", "logo_pickup_point"])

    client = APIClient()
    url = reverse("shipping-options")
    response = client.get(url, {"orderValueAmount": "20", "currency": "EUR"})
    assert response.status_code == 200

    body = response.json()
    home_rows = [
        row
        for row in body
        if row["providerCode"] == "acs" and row["kind"] == "home_delivery"
    ]
    pickup_rows = [
        row
        for row in body
        if row["providerCode"] == "acs" and row["kind"] == "pickup_point"
    ]
    assert home_rows and pickup_rows, "ACS rows missing from options response"
    # The two rows must surface DIFFERENT logo URLs.
    assert home_rows[0]["logoUrl"] != pickup_rows[0]["logoUrl"]
    assert "acs-home" in home_rows[0]["logoUrl"]
    assert "acs-smartpoint" in pickup_rows[0]["logoUrl"]


@patch(
    "extra_settings.models.Setting.get",
    side_effect=_setting_get_with_smartpoint_enabled,
)
def test_options_endpoint_pickup_kind_falls_back_to_primary_logo(_mock_setting):
    """When only ``logo`` is uploaded (no pickup-specific variant),
    both home_delivery and pickup_point rows share the same URL —
    the existing single-logo behaviour, preserved by
    ``logo_for_kind``'s fallback.
    """
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-shared.png")
    # logo_pickup_point intentionally NOT set
    provider.save(update_fields=["logo"])

    client = APIClient()
    url = reverse("shipping-options")
    response = client.get(url, {"orderValueAmount": "20", "currency": "EUR"})
    body = response.json()
    acs_rows = [row for row in body if row["providerCode"] == "acs"]
    assert len({row["logoUrl"] for row in acs_rows}) == 1, (
        "All ACS rows must share the same logoUrl when no pickup-"
        "specific logo is uploaded; got "
        f"{[row['logoUrl'] for row in acs_rows]}"
    )


@patch(
    "extra_settings.models.Setting.get",
    side_effect=_setting_get_with_smartpoint_enabled,
)
def test_options_endpoint_surfaces_uploaded_logo_url(_mock_setting):
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    provider = ShippingProvider.objects.get(code="acs")
    provider.logo = _make_png_upload("acs-test.png")
    provider.save(update_fields=["logo"])

    client = APIClient()
    url = reverse("shipping-options")
    response = client.get(url, {"orderValueAmount": "20", "currency": "EUR"})
    assert response.status_code == 200

    body = response.json()
    acs_rows = [row for row in body if row["providerCode"] == "acs"]
    assert acs_rows
    for row in acs_rows:
        # Either signed S3 URL or local file storage URL — both are
        # truthy strings; the contract is "non-null when uploaded".
        assert row["logoUrl"], (
            f"Expected truthy logoUrl after upload, got {row['logoUrl']!r}"
        )


def test_provider_serializer_returns_logo_fields():
    provider = ShippingProvider.objects.get(code="boxnow")
    provider.logo = _make_png_upload("boxnow-test.png")
    provider.save(update_fields=["logo"])

    # Exercise the serializer directly so the contract is verified
    # without needing the staff-gated provider-list URL.
    from shipping.serializers import ShippingProviderSerializer

    data = ShippingProviderSerializer(provider).data
    assert data["main_image_path"].startswith("media/public/uploads/shipping/")
    assert data["logo_filename"].endswith(".png")
    # ImageField under DRF returns the storage URL or None.
    assert data["logo"] is not None
