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
    assert provider.main_image_path.startswith("media/uploads/shipping/")
    assert provider.main_image_path.endswith(provider.logo_filename)


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
        assert row["mainImagePath"] == ""


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
        assert row["mainImagePath"].startswith("media/uploads/shipping/")


def test_provider_serializer_returns_logo_fields():
    provider = ShippingProvider.objects.get(code="boxnow")
    provider.logo = _make_png_upload("boxnow-test.png")
    provider.save(update_fields=["logo"])

    # Exercise the serializer directly so the contract is verified
    # without needing the staff-gated provider-list URL.
    from shipping.serializers import ShippingProviderSerializer

    data = ShippingProviderSerializer(provider).data
    assert data["main_image_path"].startswith("media/uploads/shipping/")
    assert data["logo_filename"].endswith(".png")
    # ImageField under DRF returns the storage URL or None.
    assert data["logo"] is not None
