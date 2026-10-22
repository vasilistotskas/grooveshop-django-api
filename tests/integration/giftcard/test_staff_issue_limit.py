"""How much gift-card value a staff member can create in one step.

Gift-card value is spendable money, and a staff role could issue a card of
any size, or add any amount to a balance. ``GIFT_CARD_STAFF_ISSUE_LIMIT``
(per store) caps one step; above it the store's OWNER or a platform
superuser is needed, and the step is noted on the ledger and in the admin
log.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money

from giftcard.admin import GiftCardAdmin
from giftcard.factories import GiftCardFactory
from giftcard.models import (
    GiftCard,
    GiftCardTransaction,
    GiftCardTransactionKind,
)
from giftcard.services import GiftCardService
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

SETTING = "GIFT_CARD_STAFF_ISSUE_LIMIT"


def _limit(value):
    return patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: (
            value if key == SETTING else default
        ),
    )


@pytest.fixture
def tenant():
    store = store_tenant("limit_store")
    previous = bind_store_tenant(store)
    yield store
    unbind_store_tenant(previous)


def _user(tenant, role):
    if role == "superuser":
        return stamp_platform_identity(
            UserAccountFactory(is_staff=True, is_superuser=True)
        )
    return store_staff(tenant, role)


def _request(user):
    request = RequestFactory().post("/admin/giftcard/giftcard/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return GiftCardAdmin(GiftCard, AdminSite())


def _issue_form(user, amount):
    request = _request(user)
    form_class = _admin().get_form(request)
    form = form_class(
        data={"initial_value_0": str(amount), "initial_value_1": "EUR"}
    )
    form.is_valid()
    return form


def _adjust(user, card, amount):
    # Unfold's dialog wrapper builds its own form from the POST data and
    # only runs the action once it validates; ``_form_submitted`` is the
    # hidden field the rendered dialog posts.
    request = RequestFactory().post(
        "/admin/giftcard/giftcard/",
        {
            "_form_submitted": "true",
            "amount": str(amount),
            "reason": "Goodwill",
        },
    )
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    _admin().adjust_balance(request, object_id=card.pk)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


class TestWhoMayExceedTheLimit:
    @pytest.mark.parametrize(
        ("role", "may"),
        [
            (TenantMembershipRole.STAFF, False),
            (TenantMembershipRole.ADMIN, False),
            (TenantMembershipRole.OWNER, True),
            ("superuser", True),
        ],
    )
    def test_only_the_owner_or_a_superuser(self, tenant, role, may):
        user = _user(tenant, role)

        with _limit(100):
            assert GiftCardService.over_staff_limit(user, Decimal(101)) is (
                not may
            )

    def test_the_limit_itself_is_allowed_to_everyone(self, tenant):
        user = _user(tenant, TenantMembershipRole.STAFF)

        with _limit(100):
            assert not GiftCardService.over_staff_limit(user, Decimal(100))

    def test_a_customer_carrying_staff_flags_is_not_an_owner(self, tenant):
        """A tenant-schema row's flags are residue: no stamp, no authority."""
        customer = UserAccountFactory(is_staff=True, is_superuser=True)

        with _limit(100):
            assert GiftCardService.over_staff_limit(customer, Decimal(101))

    def test_zero_turns_the_limit_off(self, tenant):
        user = _user(tenant, TenantMembershipRole.STAFF)

        with _limit(0):
            assert not GiftCardService.over_staff_limit(user, Decimal(100000))

    def test_the_default_is_declared_for_every_store(self):
        from django.conf import settings

        [entry] = [
            e for e in settings.EXTRA_SETTINGS_DEFAULTS if e["name"] == SETTING
        ]

        assert entry["type"] == "decimal"
        assert entry["value"] > 0
        assert entry["description"]


class TestIssuingACard:
    def test_staff_cannot_issue_above_the_limit(self, tenant):
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limit(100):
            form = _issue_form(staff, "250.00")

        assert "initial_value" in form.errors
        assert "owner can do it" in str(form.errors["initial_value"])

    def test_staff_can_issue_up_to_the_limit(self, tenant):
        staff = _user(tenant, TenantMembershipRole.STAFF)

        with _limit(100):
            form = _issue_form(staff, "100.00")

        assert "initial_value" not in form.errors

    def test_the_owner_can_issue_above_it_and_the_ledger_says_so(self, tenant):
        owner = _user(tenant, TenantMembershipRole.OWNER)
        request = _request(owner)
        admin = _admin()

        with _limit(100):
            form = _issue_form(owner, "250.00")
            assert "initial_value" not in form.errors
            card = GiftCard(initial_value=Money(Decimal("250.00"), "EUR"))
            admin.save_model(request, card, form, change=False)

        row = GiftCardTransaction.objects.get(
            gift_card__code=card.code, kind=GiftCardTransactionKind.ISSUE
        )
        assert row.created_by == owner
        assert "above the staff limit of 100" in row.description

    def test_an_issue_within_the_limit_has_the_plain_description(self, tenant):
        staff = _user(tenant, TenantMembershipRole.STAFF)
        card = GiftCard(initial_value=Money(Decimal("50.00"), "EUR"))

        with _limit(100):
            _admin().save_model(_request(staff), card, None, change=False)

        row = GiftCardTransaction.objects.get(
            gift_card__code=card.code, kind=GiftCardTransactionKind.ISSUE
        )
        assert row.description == "Issued via admin"


class TestAddingToABalance:
    def test_staff_cannot_add_above_the_limit(self, tenant):
        staff = _user(tenant, TenantMembershipRole.STAFF)
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))

        with _limit(100):
            request = _adjust(staff, card, "500.00")

        assert not GiftCardTransaction.objects.filter(
            gift_card=card, kind=GiftCardTransactionKind.ADJUST
        ).exists()
        assert any("owner can do it" in m for m in _messages(request))

    def test_staff_can_add_within_it_and_can_always_remove(self, tenant):
        staff = _user(tenant, TenantMembershipRole.STAFF)
        card = GiftCardFactory(initial_value=Money(Decimal("300.00"), "EUR"))

        with _limit(100):
            _adjust(staff, card, "100.00")
            _adjust(staff, card, "-250.00")

        amounts = list(
            GiftCardTransaction.objects.filter(
                gift_card=card, kind=GiftCardTransactionKind.ADJUST
            )
            .order_by("id")
            .values_list("amount", flat=True)
        )
        assert amounts == [Decimal("100.00"), Decimal("-250.00")]

    def test_the_owner_adds_above_it_and_it_is_audited_twice(self, tenant):
        owner = _user(tenant, TenantMembershipRole.OWNER)
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))

        with _limit(100):
            _adjust(owner, card, "500.00")

        row = GiftCardTransaction.objects.get(
            gift_card=card, kind=GiftCardTransactionKind.ADJUST
        )
        assert row.created_by == owner
        assert "authorised by the owner" in row.description
        assert LogEntry.objects.filter(
            user=owner, object_id=str(card.pk)
        ).exists()


class TestALongReasonKeepsTheAuthorisation:
    def test_the_note_survives_a_reason_at_the_field_limit(self, tenant):
        owner = _user(tenant, TenantMembershipRole.OWNER)
        card = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
        request = RequestFactory().post(
            "/admin/giftcard/giftcard/",
            {
                "_form_submitted": "true",
                "amount": "500.00",
                "reason": "x" * 255,
            },
        )
        request.user = owner
        request.session = {}
        request._messages = FallbackStorage(request)

        with _limit(100):
            _admin().adjust_balance(request, object_id=card.pk)

        row = GiftCardTransaction.objects.get(
            gift_card=card, kind=GiftCardTransactionKind.ADJUST
        )
        assert len(row.description) == 255
        assert row.description.endswith("authorised by the owner]")
