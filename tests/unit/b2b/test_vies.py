"""VIES client + the submit-time snapshot's graceful degradation.

VIES has scheduled member-state downtime — an outage must degrade to
UNAVAILABLE and never block a profile submission (the admin decides
either way).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.factories import BusinessProfileFactory
from b2b.services import B2BService
from b2b.vies import ViesClient, ViesResult, ViesUnavailableError
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _response(payload, status=200):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestViesClient:
    def test_valid_number(self):
        payload = {
            "valid": True,
            "name": "EXAMPLE IKE",
            "address": "ERMOU 1  ATHENS",
        }
        with patch.object(
            requests.Session, "post", return_value=_response(payload)
        ):
            result = ViesClient().check_vat("EL", "123456783")

        assert result.valid is True
        assert result.name == "EXAMPLE IKE"
        assert result.address == "ERMOU 1 ATHENS"  # whitespace collapsed

    def test_invalid_number(self):
        with patch.object(
            requests.Session,
            "post",
            return_value=_response({"valid": False, "name": "", "address": ""}),
        ):
            result = ViesClient().check_vat("EL", "123456783")

        assert result.valid is False

    def test_network_error_raises_unavailable(self):
        with (
            patch.object(
                requests.Session,
                "post",
                side_effect=requests.ConnectTimeout("timed out"),
            ),
            pytest.raises(ViesUnavailableError),
        ):
            ViesClient().check_vat("EL", "123456783")

    def test_no_verdict_raises_unavailable(self):
        # VIES reports member-state outages inside a 200 body shaped
        # {actionSucceed, errorWrappers} (official swagger_publicVAT.yaml).
        with (
            patch.object(
                requests.Session,
                "post",
                return_value=_response(
                    {
                        "actionSucceed": False,
                        "errorWrappers": [
                            {"error": "MS_MAX_CONCURRENT_REQ"},
                        ],
                    }
                ),
            ),
            pytest.raises(ViesUnavailableError, match="MS_MAX_CONCURRENT_REQ"),
        ):
            ViesClient().check_vat("EL", "123456783")


class TestSubmitProfileSnapshot:
    PROFILE_DATA = {
        "company_name": "Example IKE",
        "vat_id": "123456783",
        "tax_office": "Α' Αθηνών",
        "activity": "Retail",
    }

    def test_valid_snapshot(self):
        user = UserAccountFactory()
        with patch.object(
            ViesClient,
            "check_vat",
            return_value=ViesResult(
                valid=True, name="EXAMPLE IKE", address="ERMOU 1"
            ),
        ):
            profile = B2BService.submit_profile(user, dict(self.PROFILE_DATA))

        assert profile.status == BusinessProfileStatus.PENDING
        assert profile.vies_status == ViesStatus.VALID
        assert profile.vies_name == "EXAMPLE IKE"
        assert profile.vies_checked_at is not None

    def test_outage_degrades_but_submission_succeeds(self):
        user = UserAccountFactory()
        with patch.object(
            ViesClient,
            "check_vat",
            side_effect=ViesUnavailableError("boom"),
        ):
            profile = B2BService.submit_profile(user, dict(self.PROFILE_DATA))

        assert profile.pk is not None
        assert profile.vies_status == ViesStatus.UNAVAILABLE
        assert profile.vies_error == "boom"

    def test_identity_edit_while_approved_resets_to_pending(self):
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED, approved=True
        )
        data = {
            **self.PROFILE_DATA,
            "company_name": profile.company_name,
            "vat_id": "094014201",  # changed
            "tax_office": profile.tax_office,
            "activity": profile.activity,
        }
        with patch.object(
            ViesClient,
            "check_vat",
            return_value=ViesResult(valid=True, name="", address=""),
        ):
            updated = B2BService.submit_profile(profile.user, data)

        assert updated.pk == profile.pk
        assert updated.status == BusinessProfileStatus.PENDING
        # The group assignment survives — re-approval is one click.
        assert updated.customer_group is not None

    def test_address_only_edit_keeps_approved_status(self):
        profile = BusinessProfileFactory(approved=True)
        data = {
            "company_name": profile.company_name,
            "vat_id": profile.vat_id,
            "tax_office": profile.tax_office,
            "activity": profile.activity,
            "billing_street": "New Street",
            "billing_city": "Athens",
        }
        with patch.object(
            ViesClient,
            "check_vat",
            return_value=ViesResult(valid=True, name="", address=""),
        ):
            updated = B2BService.submit_profile(profile.user, data)

        assert updated.status == BusinessProfileStatus.APPROVED
        assert updated.billing_street == "New Street"

    def test_rejected_profile_reenters_pending_on_any_resubmit(
        self, django_capture_on_commit_callbacks
    ):
        """The rejection email promises re-review — an address-only fix
        must re-enter the PENDING queue too, not stay invisible."""
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.REJECTED,
            rejection_reason="Fix the billing address",
        )
        data = {
            "company_name": profile.company_name,
            "vat_id": profile.vat_id,
            "tax_office": profile.tax_office,
            "activity": profile.activity,
            "billing_street": "Corrected Street",
            "billing_city": "Athens",
        }
        with (
            patch.object(
                ViesClient,
                "check_vat",
                return_value=ViesResult(valid=True, name="", address=""),
            ),
            patch(
                "b2b.tasks.send_admin_new_business_profile_email.apply_async"
            ) as admin_mail,
            django_capture_on_commit_callbacks(execute=True),
        ):
            updated = B2BService.submit_profile(profile.user, data)

        assert updated.status == BusinessProfileStatus.PENDING
        admin_mail.assert_called_once()

    def test_suspended_profile_stays_suspended_on_resubmit(self):
        profile = BusinessProfileFactory(
            approved=True,
        )
        B2BService.suspend(profile, reviewed_by=UserAccountFactory())
        data = {
            "company_name": "Different Name IKE",
            "vat_id": "094014201",
            "tax_office": profile.tax_office,
            "activity": profile.activity,
        }
        with patch.object(
            ViesClient,
            "check_vat",
            return_value=ViesResult(valid=True, name="", address=""),
        ):
            updated = B2BService.submit_profile(profile.user, data)

        assert updated.status == BusinessProfileStatus.SUSPENDED

    def test_vies_skipped_when_identity_unchanged_and_fresh(self):
        """Identity-unchanged resubmits with a fresh verdict must NOT
        re-dial VIES — PUT /b2b/profile would otherwise be an outbound
        HTTP amplifier."""
        profile = BusinessProfileFactory(approved=True)
        with patch.object(
            ViesClient,
            "check_vat",
            return_value=ViesResult(valid=True, name="X", address="Y"),
        ) as first_check:
            B2BService.submit_profile(
                profile.user,
                {
                    "company_name": profile.company_name,
                    "vat_id": profile.vat_id,
                    "tax_office": profile.tax_office,
                    "activity": profile.activity,
                },
            )
        assert first_check.call_count == 1  # UNCHECKED → refreshed

        with patch.object(ViesClient, "check_vat") as second_check:
            B2BService.submit_profile(
                profile.user,
                {
                    "company_name": profile.company_name,
                    "vat_id": profile.vat_id,
                    "tax_office": profile.tax_office,
                    "activity": profile.activity,
                    "billing_city": "Athens",
                },
            )
        second_check.assert_not_called()

    def test_first_submit_queues_admin_notification(
        self, django_capture_on_commit_callbacks
    ):
        user = UserAccountFactory()
        with (
            patch.object(
                ViesClient,
                "check_vat",
                return_value=ViesResult(valid=True, name="", address=""),
            ),
            patch(
                "b2b.tasks.send_admin_new_business_profile_email.apply_async"
            ) as admin_mail,
            django_capture_on_commit_callbacks(execute=True),
        ):
            B2BService.submit_profile(user, dict(self.PROFILE_DATA))

        admin_mail.assert_called_once()
