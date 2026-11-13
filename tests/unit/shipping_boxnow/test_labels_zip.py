"""The "Download voucher PDFs (zip)" changelist action.

``BoxNowService.fetch_labels_zip`` builds the archive, the way
``AcsService.fetch_labels_pdf`` merges the ACS labels; the admin action
only reports the outcome and returns the download.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from shipping_boxnow.admin import BoxNowShipmentAdmin
from shipping_boxnow.exceptions import BoxNowAPIError
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowShipment
from shipping_boxnow.services import BoxNowService

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PDF = b"%PDF-1.4 label"


def _members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _refuse(loser):
    def fetch(shipment):
        if shipment.pk == loser.pk:
            raise BoxNowAPIError(500, message="BoxNow said no")
        return PDF

    return fetch


class TestService:
    def test_every_label_lands_in_one_archive(self):
        first = BoxNowShipmentFactory(with_parcel=True)
        second = BoxNowShipmentFactory(with_parcel=True)

        with patch.object(BoxNowService, "fetch_label_bytes", return_value=PDF):
            archive, failures = BoxNowService.fetch_labels_zip([first, second])

        assert failures == []
        assert _members(archive) == {
            f"boxnow-voucher-{first.parcel_id}.pdf": PDF,
            f"boxnow-voucher-{second.parcel_id}.pdf": PDF,
        }

    def test_one_bad_label_does_not_stop_the_others(self):
        good = BoxNowShipmentFactory(with_parcel=True)
        bad = BoxNowShipmentFactory(with_parcel=True)

        with patch.object(
            BoxNowService, "fetch_label_bytes", side_effect=_refuse(bad)
        ):
            archive, failures = BoxNowService.fetch_labels_zip([good, bad])

        assert list(_members(archive)) == [
            f"boxnow-voucher-{good.parcel_id}.pdf"
        ]
        assert len(failures) == 1
        assert failures[0]["shipment_id"] == bad.pk
        assert failures[0]["order_id"] == bad.order_id
        assert failures[0]["parcel_id"] == bad.parcel_id
        assert "BoxNow said no" in failures[0]["error"]

    def test_a_shipment_with_no_parcel_is_left_out_not_fetched(self):
        pending = BoxNowShipmentFactory(parcel_id=None)

        with patch.object(BoxNowService, "fetch_label_bytes") as fetch:
            archive, failures = BoxNowService.fetch_labels_zip([pending])

        fetch.assert_not_called()
        assert archive == b""
        assert failures == []

    def test_total_failure_returns_no_archive(self):
        shipment = BoxNowShipmentFactory(with_parcel=True)

        with patch.object(
            BoxNowService, "fetch_label_bytes", side_effect=_refuse(shipment)
        ):
            archive, failures = BoxNowService.fetch_labels_zip([shipment])

        assert archive == b""
        assert len(failures) == 1


def _press(queryset=None):
    request = RequestFactory().post("/")
    request.user = get_user_model()(id=1, username="staff", is_superuser=True)
    request.session = {}
    request._messages = FallbackStorage(request)
    admin = BoxNowShipmentAdmin(BoxNowShipment, AdminSite())
    response = admin.download_labels_zip(
        request,
        BoxNowShipment.objects.all() if queryset is None else queryset,
    )
    return response, [str(m) for m in request._messages]


class TestAdminAction:
    def test_it_returns_the_archive_the_service_built(self):
        BoxNowShipmentFactory(with_parcel=True)

        with patch.object(
            BoxNowService, "fetch_labels_zip", return_value=(b"archive", [])
        ) as build:
            response, messages = _press()

        build.assert_called_once()
        assert response.content == b"archive"
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        assert messages == []

    def test_a_partial_failure_still_downloads_and_says_so(self):
        good = BoxNowShipmentFactory(with_parcel=True)
        bad = BoxNowShipmentFactory(with_parcel=True)

        with patch.object(
            BoxNowService, "fetch_label_bytes", side_effect=_refuse(bad)
        ):
            response, messages = _press()

        assert list(_members(response.content)) == [
            f"boxnow-voucher-{good.parcel_id}.pdf"
        ]
        assert any("1 shipment(s) failed" in m for m in messages)

    def test_total_failure_returns_no_download(self):
        shipment = BoxNowShipmentFactory(with_parcel=True)

        with patch.object(
            BoxNowService, "fetch_label_bytes", side_effect=_refuse(shipment)
        ):
            response, messages = _press()

        assert response is None
        assert any("All 1 shipment(s) failed" in m for m in messages)

    def test_a_selection_with_no_parcel_has_nothing_to_bundle(self):
        BoxNowShipmentFactory(parcel_id=None)

        with patch.object(BoxNowService, "fetch_label_bytes") as fetch:
            response, messages = _press()

        fetch.assert_not_called()
        assert response is None
        assert any("nothing to bundle" in m for m in messages)
