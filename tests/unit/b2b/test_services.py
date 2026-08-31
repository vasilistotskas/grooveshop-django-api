"""B2BService gating and group resolution.

``resolve_group`` is the single decision point for "does this request
get wholesale prices" — cart binding, order create and /b2b/prices all
delegate to it, so the matrix lives here.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser

from b2b.enum import BusinessProfileStatus
from b2b.factories import BusinessProfileFactory, CustomerGroupFactory
from b2b.services import B2BService
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


class TestIsEnabled:
    @pytest.mark.parametrize(
        ("plan", "setting", "expected"),
        [
            (True, True, True),
            (True, False, False),
            (False, True, False),
            (False, False, False),
        ],
    )
    def test_plan_and_setting_must_both_hold(self, plan, setting, expected):
        def _get(key, default=None):
            return {"B2B_WHOLESALE_ENABLED": setting}.get(key, default)

        with (
            patch("tenant.membership.tenant_plan_allows", return_value=plan),
            patch("b2b.services.Setting.get", side_effect=_get),
        ):
            assert B2BService.is_enabled() is expected

    def test_disabled_by_default(self):
        # EXTRA_SETTINGS_DEFAULTS ships B2B_WHOLESALE_ENABLED=False —
        # the program is dark until the merchant flips it.
        assert B2BService.is_enabled() is False


class TestResolveGroup:
    def test_approved_profile_with_active_group(
        self, enable_b2b, approved_buyer
    ):
        user, group = approved_buyer(discount="15.00")

        assert B2BService.resolve_group(user) == group

    def test_none_for_anonymous_and_missing_user(self, enable_b2b):
        assert B2BService.resolve_group(None) is None
        assert B2BService.resolve_group(AnonymousUser()) is None

    def test_none_without_profile(self, enable_b2b):
        assert B2BService.resolve_group(UserAccountFactory()) is None

    @pytest.mark.parametrize(
        "status",
        [
            BusinessProfileStatus.PENDING,
            BusinessProfileStatus.REJECTED,
            BusinessProfileStatus.SUSPENDED,
        ],
    )
    def test_none_unless_approved(self, enable_b2b, status):
        profile = BusinessProfileFactory(
            status=status, customer_group=CustomerGroupFactory()
        )

        assert B2BService.resolve_group(profile.user) is None

    def test_none_without_group(self, enable_b2b):
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED, customer_group=None
        )

        assert B2BService.resolve_group(profile.user) is None

    def test_none_for_inactive_group(self, enable_b2b):
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED,
            customer_group=CustomerGroupFactory(is_active=False),
        )

        assert B2BService.resolve_group(profile.user) is None

    def test_none_when_feature_disabled(self, approved_buyer):
        user, _group = approved_buyer()

        # No enable_b2b fixture — the setting defaults to False.
        assert B2BService.resolve_group(user) is None


class TestPromotionsAllowed:
    def test_defaults_off(self):
        assert B2BService.promotions_allowed() is False

    def test_merchant_opt_in(self):
        def _get(key, default=None):
            return {"B2B_ALLOW_PROMOTIONS": True}.get(key, default)

        with patch("b2b.services.Setting.get", side_effect=_get):
            assert B2BService.promotions_allowed() is True


class TestProfileWorkflow:
    def test_approve_assigns_group_and_queues_email(
        self, django_capture_on_commit_callbacks
    ):
        profile = BusinessProfileFactory()
        group = CustomerGroupFactory()
        reviewer = UserAccountFactory(is_staff=True)

        with (
            patch(
                "b2b.tasks.send_business_profile_status_email.apply_async"
            ) as mock_task,
            django_capture_on_commit_callbacks(execute=True),
        ):
            B2BService.approve(profile, group=group, reviewed_by=reviewer)

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.APPROVED
        assert profile.customer_group == group
        assert profile.reviewed_by == reviewer
        assert profile.reviewed_at is not None
        mock_task.assert_called_once()

    def test_reject_records_reason_and_queues_email(
        self, django_capture_on_commit_callbacks
    ):
        profile = BusinessProfileFactory()
        reviewer = UserAccountFactory(is_staff=True)

        with (
            patch(
                "b2b.tasks.send_business_profile_status_email.apply_async"
            ) as mock_task,
            django_capture_on_commit_callbacks(execute=True),
        ):
            B2BService.reject(
                profile, reason="No wholesale terms", reviewed_by=reviewer
            )

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.REJECTED
        assert profile.rejection_reason == "No wholesale terms"
        mock_task.assert_called_once()

    def test_suspend_sends_no_email(self, django_capture_on_commit_callbacks):
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED,
            customer_group=CustomerGroupFactory(),
        )

        with (
            patch(
                "b2b.tasks.send_business_profile_status_email.apply_async"
            ) as mock_task,
            django_capture_on_commit_callbacks(execute=True),
        ):
            B2BService.suspend(
                profile, reviewed_by=UserAccountFactory(is_staff=True)
            )

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.SUSPENDED
        # Group assignment survives a suspension — reinstating is one
        # approve away.
        assert profile.customer_group is not None
        mock_task.assert_not_called()

    def test_percent_zero_group_still_resolves(
        self, enable_b2b, approved_buyer
    ):
        user, group = approved_buyer(discount="0.00")
        assert group.discount_percent == Decimal("0.00")
        assert B2BService.resolve_group(user) == group
