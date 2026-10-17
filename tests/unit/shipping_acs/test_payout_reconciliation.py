"""Closing a payout row no shipment explains: ``Mark as reconciled``.

The dashboard counts every payout with no shipment that nobody has
reconciled (``attention.unattributed_payouts``, no time window). Staff
clear a row with the payout list's dialog action, which asks for a note
and stamps who, when and why; the payout itself is never edited.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone, translation
from djmoney.money import Money

from shipping_acs.admin import AcsCodPayoutAdmin
from shipping_acs.attention import unattributed_payouts
from shipping_acs.exceptions import AcsPayoutReconciliationError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout
from shipping_acs.services import AcsService
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _acs_configured():
    with patch("shipping_acs.config.is_configured", return_value=True):
        yield


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def payout_admin():
    return AcsCodPayoutAdmin(AcsCodPayout, AdminSite())


def _payout(voucher: str = "2431665832", **fields) -> AcsCodPayout:
    return AcsCodPayout.objects.create(
        voucher_no=voucher,
        cod_payment_date=timezone.localdate(),
        cod_amount_total=Money(Decimal("24.97"), "EUR"),
        **fields,
    )


def _request(staff_user, method="post", data=None):
    """A real request: unfold's dialog wrapper builds its own form from
    ``request.POST`` and only runs the body once it validates."""
    factory = RequestFactory()
    path = "/admin/shipping_acs/acscodpayout/"
    request = (
        factory.post(path, {"_form_submitted": "true", **(data or {})})
        if method == "post"
        else factory.get(path)
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


class TestService:
    def test_stamps_who_when_and_why(self, staff_user):
        payout = _payout()

        AcsService.reconcile_cod_payout(
            payout, user=staff_user, note="  Sold at the counter  "
        )

        payout.refresh_from_db()
        assert payout.reconciled_at is not None
        assert payout.reconciled_by_id == staff_user.pk
        assert payout.reconciliation_note == "Sold at the counter"

    def test_a_blank_note_is_refused(self, staff_user):
        payout = _payout()

        with pytest.raises(AcsPayoutReconciliationError):
            AcsService.reconcile_cod_payout(payout, user=staff_user, note="   ")

        payout.refresh_from_db()
        assert payout.reconciled_at is None

    def test_a_payout_tied_to_a_shipment_is_refused(self, staff_user):
        payout = _payout(shipment=AcsShipmentFactory(voucher_no="2431665832"))

        with pytest.raises(AcsPayoutReconciliationError):
            AcsService.reconcile_cod_payout(payout, user=staff_user, note="x")

        payout.refresh_from_db()
        assert payout.reconciled_at is None

    def test_a_second_reconciliation_is_refused_and_keeps_the_first(
        self, staff_user
    ):
        payout = _payout()
        AcsService.reconcile_cod_payout(payout, user=staff_user, note="first")

        with pytest.raises(AcsPayoutReconciliationError):
            AcsService.reconcile_cod_payout(
                payout, user=staff_user, note="second"
            )

        payout.refresh_from_db()
        assert payout.reconciliation_note == "first"


class TestAdminAction:
    def test_get_shows_the_note_form(self, payout_admin, staff_user):
        payout = _payout()

        response = payout_admin.mark_reconciled(
            _request(staff_user, "get"), object_id=payout.pk
        )

        assert response.status_code == 200
        assert 'name="note"' in response.content.decode()
        payout.refresh_from_db()
        assert payout.reconciled_at is None

    def test_post_sets_the_fields_and_the_count_drops(
        self, payout_admin, staff_user
    ):
        payout = _payout()
        _payout("", row_fingerprint="d" * 64)
        assert unattributed_payouts().count() == 2

        response = payout_admin.mark_reconciled(
            _request(staff_user, data={"note": "Counter sale"}),
            object_id=payout.pk,
        )

        payout.refresh_from_db()
        assert payout.reconciled_at is not None
        assert payout.reconciled_by_id == staff_user.pk
        assert payout.reconciliation_note == "Counter sale"
        assert unattributed_payouts().count() == 1
        assert response.headers["HX-Redirect"] == reverse(
            "admin:shipping_acs_acscodpayout_changelist"
        )

    def test_post_requires_a_note(self, payout_admin, staff_user):
        payout = _payout()

        payout_admin.mark_reconciled(_request(staff_user), object_id=payout.pk)

        payout.refresh_from_db()
        assert payout.reconciled_at is None
        assert unattributed_payouts().count() == 1

    def test_post_reports_a_refusal_instead_of_recording(
        self, payout_admin, staff_user
    ):
        payout = _payout(reconciled_at=timezone.now())
        request = _request(staff_user, data={"note": "again"})

        with translation.override("en"):
            payout_admin.mark_reconciled(request, object_id=payout.pk)

        payout.refresh_from_db()
        assert payout.reconciliation_note == ""
        assert "already reconciled" in str(next(iter(request._messages)))

    def test_it_is_offered_on_the_row_and_the_detail_page(self, payout_admin):
        assert "mark_reconciled" in payout_admin.actions_row
        assert "mark_reconciled" in payout_admin.actions_detail
