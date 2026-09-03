"""The "Issue ACS pickup list now" changelist action.

ACS rejects the whole pickup list when any voucher on it is unprinted,
and its API cannot issue a partial one, so a single late order blocks
every other parcel that day (prod 2026-09-03). Printing the missing
label clears it in seconds — without a button the merchant still waits
for tomorrow's 16:30 run.

It lives on the SHIPMENT admin on purpose: ``AcsPickupListAdmin`` is
``IsSuperuserOnlyModelAdmin``, so the person who prints the labels
cannot open it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from shipping_acs.admin import AcsShipmentAdmin
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.models import AcsShipment

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_and_request():
    admin = AcsShipmentAdmin(AcsShipment, AdminSite())
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=7, username="staff")
    return admin, request


def _issue(admin, request, **patch_kwargs):
    """Run the action with the service patched, capturing messages."""
    with (
        patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            **patch_kwargs,
        ) as mock_issue,
        patch.object(admin, "message_user") as mock_message,
    ):
        response = admin.issue_pickup_list_now(request)
    return response, mock_issue, mock_message


class TestIssuePickupListNowAction:
    def test_is_registered_on_the_changelist(self, admin_and_request):
        admin, _ = admin_and_request
        assert "issue_pickup_list_now" in admin.actions_list

    def test_issues_and_reports_the_manifest(
        self, admin_and_request, django_user_model
    ):
        admin, request = admin_and_request
        from shipping_acs.factories import AcsPickupListFactory

        pickup_list = AcsPickupListFactory(voucher_count=3)

        response, mock_issue, mock_message = _issue(
            admin, request, return_value=pickup_list
        )

        # Attributed to the admin who pressed it, not the beat task.
        assert mock_issue.call_args.kwargs["issued_by_id"] == 7
        text = str(mock_message.call_args[0][1])
        assert pickup_list.pickup_list_no in text
        assert "3" in text
        # A view MUST return a response — the neighbouring row actions
        # return None, which Django rejects outright.
        assert response.status_code == 302

    def test_says_so_when_nothing_is_waiting(self, admin_and_request):
        admin, request = admin_and_request

        response, _, mock_message = _issue(admin, request, return_value=None)

        assert "No vouchers" in str(mock_message.call_args[0][1])
        assert response.status_code == 302

    def test_surfaces_acs_own_refusal_text(self, admin_and_request):
        admin, request = admin_and_request
        refusal = AcsAPIError(
            alias="ACS_Issue_Pickup_List",
            error_message="Βρέθηκαν 1 ατύπωτες αποστολές.",
            raw={"Unprinted_Vouchers": ["9804128445"]},
        )

        response, _, mock_message = _issue(admin, request, side_effect=refusal)

        # ACS names what has to be printed; paraphrasing loses that.
        assert "ατύπωτες" in str(mock_message.call_args[0][1])
        assert response.status_code == 302

    def test_a_refusal_never_escapes_as_a_500(self, admin_and_request):
        admin, request = admin_and_request
        from shipping_acs.exceptions import AcsError

        response, _, _ = _issue(
            admin, request, side_effect=AcsError("ACS unreachable")
        )

        assert response.status_code == 302
