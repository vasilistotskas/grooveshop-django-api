"""A refund attempt whose outcome is unknown holds its money until resolved.

No timer frees it. The provider's webhook, the reconciliation task and
staff (the "Record refund outcome" action) resolve it, each through the
ledger paths a refund made in one go takes; what the lookup cannot decide
stays PENDING and is counted on the store's dashboard.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils import timezone

from admin.dashboard.store import widgets
from order.admin import OrderAdmin
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.models.refund_attempt import RefundAttempt, RefundAttemptStatus
from order.payment_ledger import PaymentLedger
from order.refund_reconciliation import record_outcome
from order.services import OrderService
from order.tasks import reconcile_pending_refunds
from pay_way.factories import PayWayFactory
from tests.integration.order.test_refund_attempts import (
    VIVA_PAYMENT,
    _eur,
    _order,
    _rows,
    _viva_order,
)
from tests.unit.admin.dashboard.test_store_widgets import (
    _context,
    _superuser,
)
from tests.utils.stripe_refunds import FakeStripeRefunds
from tests.utils.viva import FakeVivaApi
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

ORDER_CODE = 6810910170372602


def _age(order, hours=2):
    RefundAttempt.objects.filter(order=order).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )


def _only_attempt(order) -> RefundAttempt:
    return order.refund_attempts.get()


def _stripe_unknown(order, amount="40.00", **fake):
    """A refund Stripe made whose answer was lost: its attempt is PENDING."""
    stripe_api = FakeStripeRefunds(lose_answer=True, **fake)
    with stripe_api:
        result = OrderService.refund_order(order, amount=_eur(amount))
    assert result.declined["outcome_unknown"] is True
    return stripe_api


def _viva_unknown(order, amount="40.00"):
    """A Viva refund whose answer never came: its attempt is PENDING."""
    order.metadata = {"viva_order_codes": [ORDER_CODE]}
    order.save(update_fields=["metadata"])
    with FakeVivaApi(captured=100.0, cancel=(500, "boom")):
        result = OrderService.refund_order(order, amount=_eur(amount))
    assert result.declined["outcome_unknown"] is True


def _viva_row(refund_id, amount, *, status="F", type_id=7, parent=VIVA_PAYMENT):
    return {
        "TransactionId": refund_id,
        "ParentId": parent,
        "Amount": -amount,
        "StatusId": status,
        "TransactionTypeId": type_id,
    }


def _viva_listing(*rows):
    charge = {
        "TransactionId": VIVA_PAYMENT,
        "ParentId": None,
        "Amount": 100.0,
        "StatusId": "F",
        "TransactionTypeId": 5,
    }
    return {str(ORDER_CODE): (200, {"Transactions": [charge, *rows]})}


class TestPendingHoldsUntilResolved:
    def test_an_old_pending_attempt_still_holds_its_room(self):
        order = _order()
        _stripe_unknown(order, "80.00")
        _age(order, hours=24 * 30)

        assert PaymentLedger.refundable(order) == Decimal("20.00")
        with FakeStripeRefunds() as stripe_api, pytest.raises(PaymentError):
            OrderService.refund_order(order, amount=_eur("30.00"))
        assert stripe_api.calls == []

    def test_a_webhook_refund_settles_an_old_pending_attempt(self):
        order = _order()
        charge = order.payment_transactions.get()
        attempt = RefundAttempt.objects.create(
            order=order, charge=charge, amount=_eur("40.00")
        )
        _age(order, hours=24 * 30)

        row, _ = PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("40.00"), reference="re_late"
        )

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert attempt.refund == row
        assert PaymentLedger.refundable(order) == Decimal("60.00")

    def test_a_retry_past_the_lookup_threshold_never_resends_the_key(self):
        """Stripe forgets a key after 24 hours; an overdue attempt is looked
        up, not sent again."""
        order = _order()
        _stripe_unknown(order)
        _age(order)

        with (
            FakeStripeRefunds() as stripe_api,
            pytest.raises(PaymentError) as e,
        ):
            OrderService.refund_order(order, amount=_eur("40.00"))

        assert "still not known" in str(e.value)
        assert stripe_api.calls == []


class TestStripeLookup:
    def test_a_refund_stripe_made_is_recorded(self, refunded_signals):
        order = _order()
        stripe_api = _stripe_unknown(order)
        _age(order)

        with stripe_api:
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 1, "declined": 0, "unresolved": 0}
        attempt = _only_attempt(order)
        [row] = _rows(order)
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert attempt.refund == row
        assert row.provider_reference == stripe_api.created[0].id
        assert PaymentLedger.refundable(order) == Decimal("60.00")
        assert len(refunded_signals) == 1
        assert stripe_api.list_calls[0]["payment_intent"] == "pi_ledger"

    def test_a_refund_stripe_reports_failed_is_declined(self):
        order = _order()
        stripe_api = _stripe_unknown(order, status="failed")
        _age(order)

        with stripe_api:
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 1, "unresolved": 0}
        assert _only_attempt(order).status == RefundAttemptStatus.DECLINED
        assert PaymentLedger.refundable(order) == Decimal("100.00")
        assert not _rows(order).exists()

    def test_nothing_found_stays_pending(self):
        order = _order()
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, amount=_eur("40.00"))
        _age(order)

        with FakeStripeRefunds():
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 1}
        assert _only_attempt(order).status == RefundAttemptStatus.PENDING
        assert PaymentLedger.refundable(order) == Decimal("60.00")

    def test_a_provider_error_stays_pending(self):
        order = _order()
        stripe_api = _stripe_unknown(order)
        stripe_api.list_fails_with = stripe.APIConnectionError("drop")
        _age(order)

        with stripe_api:
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 1}
        assert _only_attempt(order).status == RefundAttemptStatus.PENDING

    def test_a_young_attempt_is_not_looked_up(self):
        order = _order()
        stripe_api = _stripe_unknown(order)

        with stripe_api:
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 0}
        assert stripe_api.list_calls == []

    def test_a_refund_booked_for_another_attempt_settles_without_booking_twice(
        self,
    ):
        order = _order()
        stripe_api = _stripe_unknown(order)
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        mine = _only_attempt(order)
        older = RefundAttempt.objects.create(
            order=order, charge=charge, amount=_eur("40.00")
        )
        RefundAttempt.objects.filter(pk=older.pk).update(
            created_at=mine.created_at - timedelta(minutes=5)
        )
        # The refund's webhook books it against the older attempt of the
        # same amount, as it cannot tell them apart.
        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=_eur("40.00"),
            reference=stripe_api.created[0].id,
        )
        older.refresh_from_db()
        assert older.status == RefundAttemptStatus.SETTLED
        _age(order)

        with stripe_api:
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 1, "declined": 0, "unresolved": 0}
        mine.refresh_from_db()
        assert mine.status == RefundAttemptStatus.SETTLED
        assert _rows(order).count() == 1
        assert PaymentLedger.refundable(order) == Decimal("60.00")


class TestVivaLookup:
    def test_a_refund_viva_made_is_recorded(self):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)

        with FakeVivaApi(
            by_order_code=_viva_listing(_viva_row("refund-tx-1", 40.0))
        ):
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 1, "declined": 0, "unresolved": 0}
        [row] = _rows(order)
        assert row.provider_reference == "refund-tx-1"
        assert _only_attempt(order).refund == row
        assert PaymentLedger.refundable(order) == Decimal("60.00")

    def test_a_refund_viva_reports_in_error_is_declined(self):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)

        with FakeVivaApi(
            by_order_code=_viva_listing(
                _viva_row("refund-tx-1", 40.0, status="E")
            )
        ):
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 1, "unresolved": 0}
        assert PaymentLedger.refundable(order) == Decimal("100.00")

    @pytest.mark.parametrize(
        "rows",
        [
            [],
            [_viva_row("a", 40.0), _viva_row("b", 40.0)],
            [_viva_row("a", 25.0)],
            [_viva_row("a", 40.0, parent="another-payment")],
            [_viva_row("a", 40.0, type_id=5)],
            [_viva_row("a", 40.0, status="A")],
        ],
        ids=[
            "nothing",
            "ambiguous",
            "other-amount",
            "other-payment",
            "not-a-refund",
            "undecided",
        ],
    )
    def test_what_cannot_be_decided_stays_pending(self, rows):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)

        with FakeVivaApi(by_order_code=_viva_listing(*rows)):
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 1}
        assert _only_attempt(order).status == RefundAttemptStatus.PENDING
        assert PaymentLedger.refundable(order) == Decimal("60.00")

    def test_a_refund_the_ledger_holds_is_not_given_to_a_second_attempt(self):
        order = _viva_order()
        _viva_unknown(order)
        charge = order.payment_transactions.get()
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.REFUND,
            provider="viva_wallet",
            provider_reference="refund-tx-1",
            amount=_eur("40.00"),
        )
        _age(order)

        with FakeVivaApi(
            by_order_code=_viva_listing(_viva_row("refund-tx-1", 40.0))
        ):
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 1}

    def test_viva_unreadable_stays_pending(self):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)

        with FakeVivaApi(by_order_code={str(ORDER_CODE): (500, "boom")}):
            counts = reconcile_pending_refunds()

        assert counts == {"settled": 0, "declined": 0, "unresolved": 1}


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


def _post(staff_user, data):
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


class TestRecordRefundOutcomeAction:
    def _overdue_order(self):
        order = _order()
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, amount=_eur("40.00"))
        _age(order)
        return order, _only_attempt(order)

    def _overdue_order_stripe_made(self):
        """Stripe made the refund (``re_1``) and the answer was lost."""
        order = _order()
        stripe_api = _stripe_unknown(order)
        _age(order)
        return order, _only_attempt(order), stripe_api

    def test_it_is_a_detail_action(self, order_admin):
        assert "record_refund_outcome" in order_admin.actions_detail

    def test_staff_record_a_refund_that_was_made(
        self, order_admin, staff_user, refunded_signals
    ):
        order, attempt, stripe_api = self._overdue_order_stripe_made()
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": "re_1",
            },
        )

        with stripe_api:
            response = order_admin.record_refund_outcome(
                request, object_id=order.pk
            )

        assert response.headers["HX-Redirect"].endswith(
            f"/order/order/{order.pk}/change/"
        )
        attempt.refresh_from_db()
        [row] = _rows(order)
        assert row.provider_reference == "re_1"
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert attempt.refund == row
        assert len(refunded_signals) == 1
        note = OrderHistory.objects.filter(
            order=order, change_type="NOTE"
        ).latest("id")
        assert note.user == staff_user
        assert "re_1" in note.new_value["note"]
        assert _messages(request) == ["The refund outcome is recorded."]

    def test_staff_record_a_refund_that_was_not_made(
        self, order_admin, staff_user
    ):
        order, attempt = self._overdue_order()
        request = _post(
            staff_user, {"attempt": attempt.pk, "outcome": "failed"}
        )

        order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.DECLINED
        assert not _rows(order).exists()
        assert PaymentLedger.refundable(order) == Decimal("100.00")
        note = OrderHistory.objects.filter(
            order=order, change_type="NOTE"
        ).latest("id")
        assert note.user == staff_user

    def test_a_made_refund_needs_its_reference(self, order_admin, staff_user):
        order, attempt = self._overdue_order()
        request = _post(
            staff_user, {"attempt": attempt.pk, "outcome": "settled"}
        )

        order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING

    def test_a_reference_already_in_the_ledger_is_refused(
        self, order_admin, staff_user
    ):
        order, attempt, stripe_api = self._overdue_order_stripe_made()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("5.00"), reference="re_1"
        )
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": "re_1",
            },
        )

        with stripe_api:
            order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING
        assert "already recorded" in _messages(request)[0]

    @pytest.mark.parametrize(
        "reference", ["re_typo", "re_other_payment", "re_other_amount"]
    )
    def test_a_reference_the_provider_cannot_confirm_is_refused(
        self, order_admin, staff_user, reference
    ):
        order, attempt, stripe_api = self._overdue_order_stripe_made()
        made = stripe_api.created[0]
        stripe_api.created.append(
            SimpleNamespace(
                id="re_other_payment",
                status="succeeded",
                amount=made.amount,
                currency="eur",
                payment_intent="pi_somebody_else",
                metadata=None,
            )
        )
        stripe_api.created.append(
            SimpleNamespace(
                id="re_other_amount",
                status="succeeded",
                amount=1000,
                currency="eur",
                payment_intent=made.payment_intent,
                metadata=None,
            )
        )
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": reference,
            },
        )

        with stripe_api:
            order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING
        assert not _rows(order).exists()
        assert "holds no refund with this reference" in _messages(request)[0]

    def test_a_refund_the_provider_reports_failed_is_not_booked_as_made(
        self, order_admin, staff_user
    ):
        order, attempt, stripe_api = self._overdue_order_stripe_made()
        stripe_api.created[0].status = "failed"
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": "re_1",
            },
        )

        with stripe_api:
            order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING
        assert "reports this refund failed" in _messages(request)[0]

    def test_a_viva_reference_is_booked_as_viva_spells_it(
        self, order_admin, staff_user
    ):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)
        attempt = _only_attempt(order)
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": "refund-tx-1",
            },
        )

        with FakeVivaApi(
            by_order_code=_viva_listing(_viva_row("Refund-TX-1", 40.0))
        ):
            order_admin.record_refund_outcome(request, object_id=order.pk)

        [row] = _rows(order)
        assert row.provider_reference == "Refund-TX-1"

    def test_a_reference_taken_in_another_case_is_refused(
        self, order_admin, staff_user
    ):
        order = _viva_order()
        _viva_unknown(order)
        _age(order)
        attempt = _only_attempt(order)
        PaymentTransactionFactory(
            order=order,
            charge=order.payment_transactions.get(),
            kind=PaymentTransactionKind.REFUND,
            provider="viva_wallet",
            provider_reference="REFUND-TX-1",
            amount=_eur("5.00"),
        )
        request = _post(
            staff_user,
            {
                "attempt": attempt.pk,
                "outcome": "settled",
                "reference": "refund-tx-1",
            },
        )

        with FakeVivaApi(
            by_order_code=_viva_listing(_viva_row("refund-tx-1", 40.0))
        ):
            order_admin.record_refund_outcome(request, object_id=order.pk)

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING
        assert "already recorded" in _messages(request)[0]

    def test_it_is_refused_while_the_lookup_is_still_due(
        self, order_admin, staff_user
    ):
        order = _order()
        with FakeStripeRefunds(fail_with=stripe.APIConnectionError("drop")):
            OrderService.refund_order(order, amount=_eur("40.00"))
        attempt = _only_attempt(order)

        # Not offered, and a forged request for it is refused outright.
        assert not order_admin.has_record_refund_outcome_permission(
            RequestFactory().get("/"), object_id=order.pk
        )
        with pytest.raises(PermissionDenied):
            order_admin.record_refund_outcome(
                _post(staff_user, {"attempt": attempt.pk, "outcome": "failed"}),
                object_id=order.pk,
            )
        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING
        with pytest.raises(PaymentError, match="not overdue yet"):
            record_outcome(attempt, user=staff_user, settled=False)

    def test_it_is_offered_where_a_refund_is_overdue(self, order_admin):
        order, _attempt = self._overdue_order()

        assert order_admin.has_record_refund_outcome_permission(
            RequestFactory().get("/"), object_id=order.pk
        )

    def test_a_user_who_may_not_change_orders_is_refused(self, order_admin):
        order, attempt = self._overdue_order()
        user = UserAccountFactory(is_staff=True)

        with pytest.raises(PermissionDenied):
            order_admin.record_refund_outcome(
                _post(user, {"attempt": attempt.pk, "outcome": "failed"}),
                object_id=order.pk,
            )

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.PENDING

    def test_cash_paid_back_by_the_merchant_is_recorded_with_its_reference(
        self, order_admin, staff_user
    ):
        order = OrderFactory(
            num_order_items=0,
            pay_way=PayWayFactory.create_online_payment(),
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            amount_due=_eur("50.00"),
        )
        collection = PaymentTransactionFactory(
            order=order,
            kind=PaymentTransactionKind.COD_COLLECTION,
            amount=_eur("50.00"),
            provider_reference="cod-1",
        )
        attempt = RefundAttempt.objects.create(
            order=order, charge=collection, amount=_eur("20.00")
        )
        _age(order)

        order_admin.record_refund_outcome(
            _post(
                staff_user,
                {
                    "attempt": attempt.pk,
                    "outcome": "settled",
                    "reference": "iban-1",
                },
            ),
            object_id=order.pk,
        )

        attempt.refresh_from_db()
        assert attempt.status == RefundAttemptStatus.SETTLED
        assert PaymentLedger.has_cod_refund(order, collection, "iban-1")


class TestTheDashboardAlert:
    def _titles(self):
        return [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, _superuser())["alerts"]
        ]

    def test_a_refund_with_an_unknown_outcome_is_listed(self):
        order = _order()
        _stripe_unknown(order)
        assert "Refunds with an unknown outcome" not in self._titles()

        _age(order)

        assert "Refunds with an unknown outcome" in self._titles()

    def test_resolving_it_removes_the_alert(self):
        order = _order()
        _stripe_unknown(order)
        _age(order)
        RefundAttempt.objects.filter(order=order).update(
            status=RefundAttemptStatus.DECLINED
        )

        assert "Refunds with an unknown outcome" not in self._titles()


class TestScheduled:
    def test_the_task_is_in_the_beat_schedule_through_the_fanout(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["reconcile-pending-refunds"]

        assert entry["task"] == "tenant.tasks.fanout_reconcile_pending_refunds"
