"""How much gift-card value ONE staff member can create in a day.

``GIFT_CARD_STAFF_ISSUE_LIMIT`` caps a single step, which a person can
repeat. ``GIFT_CARD_STAFF_DAILY_LIMIT`` (per store) caps what one staff
member issued or added over any rolling 24 hours, across the admin add form
and the balance adjustment together. Above it the store's OWNER or a
platform superuser is needed, and the step is noted on the ledger and in the
admin log, exactly like the per-step limit.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.db import connection, transaction
from django.utils import timezone
from djmoney.money import Money

from giftcard.factories import GiftCardFactory
from giftcard.models import (
    GiftCard,
    GiftCardTransaction,
    GiftCardTransactionKind,
)
from giftcard.services import GiftCardService
from tenant.models import TenantMembershipRole
from tests.integration.giftcard.test_staff_issue_limit import (
    _adjust,
    _admin,
    _issue_form,
    _messages,
    _request,
    _user,
    tenant,  # noqa: F401  (fixture)
)
from tests.utils.staff import stamp_platform_identity
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

STEP = "GIFT_CARD_STAFF_ISSUE_LIMIT"
DAILY = "GIFT_CARD_STAFF_DAILY_LIMIT"


def _limits(*, step=0, daily):
    values = {STEP: step, DAILY: daily}
    return patch(
        "giftcard.services.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    )


def _issue(user, amount):
    """Issue a card through the admin add form, as the page does it: the
    form is validated and the row saved in one transaction."""
    request = _request(user)
    with transaction.atomic():
        form = _issue_form(user, amount)
        if "initial_value" in form.errors:
            return form
        card = GiftCard(initial_value=Money(Decimal(amount), "EUR"))
        _admin().save_model(request, card, form, change=False)
    return form


def _added(user) -> Decimal:
    return GiftCardService.issued_by_in_last_day(user)


class TestTheDailyTotal:
    def test_issuing_and_adding_count_together(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)
        card = GiftCardFactory(initial_value=Money(Decimal("1.00"), "EUR"))

        with _limits(daily=1000):
            _issue(staff, "600.00")
            request = _adjust(staff, card, "500.00")

        assert _added(staff) == Decimal("600.00")
        assert any("in a day" in m for m in _messages(request))
        assert not GiftCardTransaction.objects.filter(
            gift_card=card, kind=GiftCardTransactionKind.ADJUST
        ).exists()

    def test_a_second_issue_over_the_day_is_refused(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limits(daily=1000):
            first = _issue(staff, "600.00")
            second = _issue(staff, "500.00")

        assert "initial_value" not in first.errors
        assert "in a day" in str(second.errors["initial_value"])
        assert _added(staff) == Decimal("600.00")

    def test_up_to_the_limit_is_allowed(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limits(daily=1000):
            _issue(staff, "600.00")
            second = _issue(staff, "400.00")

        assert "initial_value" not in second.errors
        assert _added(staff) == Decimal("1000.00")

    def test_removing_balance_does_not_make_room(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)
        card = GiftCardFactory(initial_value=Money(Decimal("1.00"), "EUR"))

        with _limits(daily=1000):
            _adjust(staff, card, "900.00")
            _adjust(staff, card, "-900.00")
            request = _adjust(staff, card, "200.00")

        assert any("in a day" in m for m in _messages(request))
        assert _added(staff) == Decimal("900.00")

    def test_a_day_later_the_room_is_back(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limits(daily=1000):
            _issue(staff, "900.00")
            GiftCardTransaction.objects.filter(created_by_id=staff.pk).update(
                created_at=timezone.now() - timedelta(hours=25)
            )
            again = _issue(staff, "900.00")

        assert "initial_value" not in again.errors

    def test_each_staff_member_has_their_own_day(self, tenant):  # noqa: F811
        first = _user(tenant, TenantMembershipRole.STAFF)
        second = _user(tenant, TenantMembershipRole.STAFF)

        with _limits(daily=1000):
            _issue(first, "900.00")
            other = _issue(second, "900.00")

        assert "initial_value" not in other.errors

    def test_a_purchased_card_is_nobodys_staff_issue(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)
        GiftCardService.issue(Money(Decimal("5000.00"), "EUR"))

        with _limits(daily=1000):
            form = _issue(staff, "900.00")

        assert "initial_value" not in form.errors

    def test_zero_turns_it_off(self, tenant):  # noqa: F811
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limits(daily=0):
            _issue(staff, "5000.00")
            again = _issue(staff, "5000.00")

        assert "initial_value" not in again.errors


class TestAboveTheDailyLimit:
    def test_the_owner_may_and_the_ledger_says_so(self, tenant):  # noqa: F811
        owner = _user(tenant, TenantMembershipRole.OWNER)

        with _limits(daily=1000):
            _issue(owner, "900.00")
            second = _issue(owner, "500.00")

        assert "initial_value" not in second.errors
        rows = GiftCardTransaction.objects.filter(
            created_by_id=owner.pk, kind=GiftCardTransactionKind.ISSUE
        ).order_by("id")
        assert "daily limit" not in rows[0].description
        assert "above the staff daily limit of 1000" in rows[1].description

    def test_an_owner_balance_adjustment_is_audited_twice(self, tenant):  # noqa: F811
        owner = _user(tenant, TenantMembershipRole.OWNER)
        card = GiftCardFactory(initial_value=Money(Decimal("1.00"), "EUR"))

        with _limits(daily=1000):
            _issue(owner, "900.00")
            _adjust(owner, card, "500.00")

        row = GiftCardTransaction.objects.get(
            gift_card=card, kind=GiftCardTransactionKind.ADJUST
        )
        assert "above the staff daily limit of 1000" in row.description
        assert LogEntry.objects.filter(
            user=owner, object_id=str(card.pk)
        ).exists()

    def test_a_platform_superuser_may(self, tenant):  # noqa: F811
        admin = _user(tenant, "superuser")

        with _limits(daily=1000):
            _issue(admin, "900.00")
            second = _issue(admin, "500.00")

        assert "initial_value" not in second.errors

    def test_a_customer_with_staff_flags_may_not(self, tenant):  # noqa: F811
        customer = UserAccountFactory(is_staff=True, is_superuser=True)

        with _limits(daily=1000):
            _issue(customer, "900.00")
            second = _issue(customer, "500.00")

        assert "in a day" in str(second.errors["initial_value"])


def test_the_default_is_declared_for_every_store():
    from django.conf import settings

    [entry] = [
        e for e in settings.EXTRA_SETTINGS_DEFAULTS if e["name"] == DAILY
    ]

    assert entry["type"] == "decimal"
    assert entry["value"] == 1000
    assert entry["description"]


@pytest.mark.django_db(transaction=True)
def test_two_requests_cannot_both_pass_on_the_same_total():
    """Both read a total of zero and each adds 600 against a limit of 1000.
    Without the per-member lock both would be written."""
    staff = stamp_platform_identity(UserAccountFactory())
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []

    def attempt():
        try:
            barrier.wait()
            with transaction.atomic():
                GiftCardService.lock_staff_issuing(staff)
                refused = GiftCardService.exceeds_daily_limit(
                    staff, Decimal(600)
                )
                # The window between reading the total and writing the row.
                time.sleep(0.5)
                if not refused:
                    GiftCardService.issue(
                        Money(Decimal(600), "EUR"), created_by=staff
                    )
                outcomes.append(not refused)
        finally:
            connection.close()

    with _limits(daily=1000):
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert sorted(outcomes) == [False, True]
    assert GiftCardService.issued_by_in_last_day(staff) == Decimal("600.00")
