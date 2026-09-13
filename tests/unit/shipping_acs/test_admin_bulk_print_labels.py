"""The "Print labels for selected shipments" changelist action.

ACS refuses the WHOLE daily manifest while any voucher on it is
unprinted, and its API cannot issue a partial one — so the operation
that unblocks a pickup list is "print everything outstanding". Until
this action existed that meant opening each order's change page in
turn, which is exactly how voucher #162 sat unprinted from 2026-09-01
and how the manifest silently failed on 09-10 and 09-11.

ACS has no bulk print alias, so the merge is ours: N cached
``ACS_Print_Voucher`` calls concatenated into one file, because one
file is one print job on the thermal roll.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from shipping_acs.admin import AcsShipmentAdmin
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_acs.services import AcsService

pytestmark = pytest.mark.django_db


def _one_page_pdf(text: str = "label") -> bytes:
    """A genuinely parseable single-page PDF.

    Built with pypdf itself rather than a hand-written byte string: the
    merge is the thing under test, so a stub that PdfReader cannot open
    would make these tests pass or fail for the wrong reason.
    """
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=288, height=432)  # ACS thermal, 4x6in
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def admin_and_request():
    admin = AcsShipmentAdmin(AcsShipment, AdminSite())
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=7, username="staff")
    return admin, request


def _candidate(voucher_no):
    return AcsShipmentFactory(
        voucher_no=voucher_no,
        shipment_state=AcsShipmentState.NEW,
        label_printed_at=None,
    )


class TestMergeService:
    def test_every_label_lands_in_one_document(self):
        first = _candidate("9800000001")
        second = _candidate("9800000002")

        with patch.object(
            AcsService, "fetch_label_bytes", return_value=_one_page_pdf()
        ):
            pdf, failures = AcsService.fetch_labels_pdf([first, second])

        assert failures == []
        from io import BytesIO

        from pypdf import PdfReader

        assert len(PdfReader(BytesIO(pdf)).pages) == 2, (
            "two vouchers must produce a two-page file — one print job, "
            "not one per order"
        )

    def test_one_bad_voucher_does_not_stop_the_others(self):
        good = _candidate("9800000001")
        bad = _candidate("9800000002")

        def _fetch(shipment):
            if shipment.pk == bad.pk:
                raise AcsAPIError(
                    alias="ACS_Print_Voucher", error_message="ACS said no"
                )
            return _one_page_pdf()

        with patch.object(AcsService, "fetch_label_bytes", side_effect=_fetch):
            pdf, failures = AcsService.fetch_labels_pdf([good, bad])

        assert pdf, (
            "a single failing voucher swallowed the whole batch — the exact "
            "all-or-nothing failure this action exists to avoid"
        )
        assert len(failures) == 1
        assert failures[0]["voucher_no"] == "9800000002"
        assert failures[0]["order_id"] == bad.order_id

    def test_a_shipment_with_no_voucher_is_reported_not_fetched(self):
        pending = AcsShipmentFactory(
            voucher_no=None,
            shipment_state=AcsShipmentState.PENDING_CREATION,
        )

        with patch.object(AcsService, "fetch_label_bytes") as mock_fetch:
            pdf, failures = AcsService.fetch_labels_pdf([pending])

        assert not mock_fetch.called
        assert pdf == b""
        assert failures[0]["error"] == "no voucher minted yet"

    def test_total_failure_returns_no_document(self):
        shipment = _candidate("9800000001")

        with patch.object(
            AcsService,
            "fetch_label_bytes",
            side_effect=AcsAPIError(
                alias="ACS_Print_Voucher", error_message="down"
            ),
        ):
            pdf, failures = AcsService.fetch_labels_pdf([shipment])

        assert pdf == b""
        assert len(failures) == 1


class TestAdminAction:
    def test_it_returns_the_merged_pdf_as_a_download(self, admin_and_request):
        admin, request = admin_and_request
        _candidate("9800000001")
        _candidate("9800000002")

        with patch.object(
            AcsService, "fetch_label_bytes", return_value=_one_page_pdf()
        ):
            response = admin.bulk_print_labels(
                request, AcsShipment.objects.all()
            )

        assert response is not None, (
            "the action returned nothing — an Unfold action must RETURN "
            "the response or the browser gets no file"
        )
        assert response["Content-Type"] == "application/pdf"
        assert "attachment" in response["Content-Disposition"]
        assert int(response["Content-Length"]) == len(response.content)

    def test_a_partial_failure_still_downloads_and_names_the_loser(
        self, admin_and_request
    ):
        admin, request = admin_and_request
        good = _candidate("9800000001")

        def _fetch(shipment):
            if shipment.pk != good.pk:
                raise AcsAPIError(
                    alias="ACS_Print_Voucher", error_message="ACS said no"
                )
            return _one_page_pdf()

        bad = _candidate("9800000002")

        with (
            patch.object(AcsService, "fetch_label_bytes", side_effect=_fetch),
            patch.object(admin, "message_user") as mock_message,
        ):
            response = admin.bulk_print_labels(
                request, AcsShipment.objects.all()
            )

        assert response is not None
        reported = " | ".join(
            str(call.args[1]) for call in mock_message.call_args_list
        )
        assert "9800000002" in reported
        assert str(bad.order_id) in reported

    def test_total_failure_returns_no_download(self, admin_and_request):
        admin, request = admin_and_request
        _candidate("9800000001")

        with (
            patch.object(
                AcsService,
                "fetch_label_bytes",
                side_effect=AcsAPIError(
                    alias="ACS_Print_Voucher", error_message="down"
                ),
            ),
            patch.object(admin, "message_user") as mock_message,
        ):
            response = admin.bulk_print_labels(
                request, AcsShipment.objects.all()
            )

        assert response is None, (
            "an empty PDF must not be offered as a download — it looks "
            "like a successful print of nothing"
        )
        assert mock_message.called
