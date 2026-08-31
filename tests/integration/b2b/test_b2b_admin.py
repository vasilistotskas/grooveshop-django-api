"""The B2B admin is where merchants actually run the programme:
approve a business, put it in a pricing tier, and load a price list.

The detail actions are the ONLY sanctioned way to move a profile's
status (the field is read-only precisely so it routes through
``B2BService`` and the notification emails stay deterministic), so they
are worth covering directly.

Dialog actions are driven through real POST data rather than a stub
form: unfold's ``@action(dialog=...)`` wrapper builds its own form from
``request.POST`` and only runs the body once that form validates, so a
hand-made form object would be silently ignored and the assertions
would pass against an unexecuted action.
"""

from decimal import Decimal

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money

from b2b.admin import BusinessProfileAdmin, CustomerGroupAdmin
from b2b.enum import BusinessProfileStatus
from b2b.factories import BusinessProfileFactory, CustomerGroupFactory
from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user(db):
    return UserAccountFactory(is_staff=True, is_superuser=True)


def _post(staff_user, data):
    # ``_form_submitted`` is BaseDialogForm's required hidden field —
    # the rendered dialog posts it, and without it the wrapper treats
    # the request as "open the dialog" and never runs the action.
    request = RequestFactory().post(
        "/admin/b2b/", {"_form_submitted": "true", **data}
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def profile_admin():
    return BusinessProfileAdmin(BusinessProfile, AdminSite())


@pytest.fixture
def group_admin():
    return CustomerGroupAdmin(CustomerGroup, AdminSite())


class TestBusinessProfileActions:
    def test_approve_assigns_group_and_stamps_reviewer(
        self, profile_admin, staff_user
    ):
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.PENDING, customer_group=None
        )
        group = CustomerGroupFactory(discount_percent=Decimal("15.00"))

        response = profile_admin.approve(
            _post(staff_user, {"customer_group": group.pk}),
            object_id=profile.pk,
        )

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.APPROVED
        assert profile.customer_group_id == group.pk
        assert profile.reviewed_by_id == staff_user.pk
        assert profile.reviewed_at is not None
        # Unfold dialogs navigate via HX-Redirect, not a 302.
        assert "HX-Redirect" in response.headers

    def test_approve_requires_a_group(self, profile_admin, staff_user):
        """An empty dialog must not silently approve at retail prices."""
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.PENDING, customer_group=None
        )

        profile_admin.approve(_post(staff_user, {}), object_id=profile.pk)

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.PENDING

    def test_approve_offers_only_active_groups(self, profile_admin, staff_user):
        """An inactive tier prices nothing — it must not be selectable."""
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.PENDING, customer_group=None
        )
        inactive = CustomerGroupFactory(is_active=False)

        profile_admin.approve(
            _post(staff_user, {"customer_group": inactive.pk}),
            object_id=profile.pk,
        )

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.PENDING

    def test_reject_records_reason(self, profile_admin, staff_user):
        profile = BusinessProfileFactory(status=BusinessProfileStatus.PENDING)

        profile_admin.reject(
            _post(staff_user, {"reason": "Missing tax documents"}),
            object_id=profile.pk,
        )

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.REJECTED
        assert profile.rejection_reason == "Missing tax documents"
        assert profile.reviewed_by_id == staff_user.pk

    def test_suspend_revokes_wholesale(self, profile_admin, staff_user):
        group = CustomerGroupFactory()
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED, customer_group=group
        )

        profile_admin.suspend(_post(staff_user, {}), object_id=profile.pk)

        profile.refresh_from_db()
        assert profile.status == BusinessProfileStatus.SUSPENDED
        assert profile.is_approved is False

    def test_status_is_read_only_so_transitions_go_through_actions(
        self, profile_admin
    ):
        for field in ("status", "vies_status", "reviewed_by", "reviewed_at"):
            assert field in profile_admin.readonly_fields

    def test_status_and_vies_labels_render(self, profile_admin):
        profile = BusinessProfileFactory(status=BusinessProfileStatus.APPROVED)

        value, label = profile_admin.status_label(profile)
        assert value == BusinessProfileStatus.APPROVED
        assert label

        value, label = profile_admin.vies_label(profile)
        assert value == profile.vies_status
        assert label


class TestImportPrices:
    def test_creates_then_updates_rows_for_this_group_only(
        self, group_admin, staff_user
    ):
        group = CustomerGroupFactory()
        other_group = CustomerGroupFactory()
        product = ProductFactory(
            sku="SKU-001", price=Money(Decimal("100.00"), "EUR")
        )

        group_admin.import_prices(
            _post(staff_user, {"lines": "SKU-001;12.50"}), object_id=group.pk
        )

        row = PriceListItem.objects.get(group=group, product=product)
        assert row.net_price.amount == Decimal("12.50")
        assert not PriceListItem.objects.filter(group=other_group).exists()

        # Re-import updates in place rather than duplicating (the model
        # has a (group, product) unique constraint). Comma doubles as a
        # decimal mark, which is what a Greek spreadsheet exports.
        group_admin.import_prices(
            _post(staff_user, {"lines": "SKU-001;9,90"}), object_id=group.pk
        )

        assert PriceListItem.objects.filter(group=group).count() == 1
        row.refresh_from_db()
        assert row.net_price.amount == Decimal("9.90")

    def test_unknown_sku_is_reported_not_raised(self, group_admin, staff_user):
        group = CustomerGroupFactory()

        group_admin.import_prices(
            _post(staff_user, {"lines": "NOPE-999;5.00"}), object_id=group.pk
        )

        assert not PriceListItem.objects.filter(group=group).exists()
