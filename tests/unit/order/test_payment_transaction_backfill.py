"""The 0067 backfill: ledger rows for the orders that were already paid.

It writes what the order rows and ``metadata["refunds"]`` PROVE, and
nothing it would have to guess: the amount is ``paid_amount`` (what the
order owed, which checkout verified against the provider), a refund with
no provider refund id is not invented, and ``payment_status`` is left
alone. It runs against the historical model registry, which is what a
migration actually receives.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.payment_transaction import PaymentTransaction
from order.payment_ledger import PaymentLedger
from pay_way.factories import PayWayFactory

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "order.migrations.0067_backfill_payment_transactions"
)
CHARGE = PaymentTransactionKind.CHARGE
REFUND = PaymentTransactionKind.REFUND


def _historical_apps():
    """The model registry a migration actually receives (see
    ``test_invoice_component_backfill``)."""
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state(
        ("order", "0067_backfill_payment_transactions")
    )
    return state.apps


def _run():
    _migration.backfill_payment_transactions(_historical_apps(), None)


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _paid(provider="stripe", payment_id="pi_bf_1", amount="50.00", **kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment(provider))
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    return OrderFactory(
        num_order_items=0,
        payment_id=payment_id,
        paid_amount=_eur(amount),
        **kwargs,
    )


def _rows(order, kind=None):
    rows = order.payment_transactions.all()
    return list(rows.filter(kind=kind) if kind else rows)


class TestCharges:
    def test_a_stripe_order_gets_its_charge(self):
        order = _paid("stripe", "pi_bf_1", "50.00")

        _run()

        [charge] = _rows(order)
        assert charge.kind == CHARGE
        assert charge.provider == "stripe"
        assert charge.provider_reference == "pi_bf_1"
        assert charge.amount == _eur("50.00")

    def test_a_viva_order_gets_its_charge(self):
        order = _paid("viva_wallet", "viva-txn-1", "21.10")

        _run()

        [charge] = _rows(order)
        assert (charge.provider, charge.provider_reference) == (
            "viva_wallet",
            "viva-txn-1",
        )
        assert charge.amount == _eur("21.10")

    def test_the_provider_is_found_without_a_pay_way_too(self):
        by_method = _paid(
            "viva_wallet", "v-1", pay_way=None, payment_method="viva_wallet"
        )
        by_prefix = _paid("stripe", "pi_bf_2", pay_way=None, payment_method="")

        _run()

        assert _rows(by_method)[0].provider == "viva_wallet"
        assert _rows(by_prefix)[0].provider == "stripe"

    @pytest.mark.parametrize(
        "payment_status",
        [PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED],
    )
    def test_an_order_refunded_since_still_had_its_charge(self, payment_status):
        order = _paid(payment_status=payment_status)

        _run()

        assert [r.kind for r in _rows(order)] == [CHARGE]

    @pytest.mark.parametrize(
        "payment_status",
        [PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.CANCELED],
    )
    def test_an_order_that_was_never_paid_gets_nothing(self, payment_status):
        order = _paid(payment_status=payment_status)

        _run()

        assert _rows(order) == []

    def test_an_order_settled_without_a_provider_gets_nothing(self):
        gift_card = _paid(payment_id="GIFTCARD_abc", amount="0.00")
        offline = _paid(payment_id="OFFLINE_7", pay_way=None, payment_method="")

        _run()

        assert _rows(gift_card) == []
        assert _rows(offline) == []

    def test_cash_collected_by_a_courier_is_a_collection_not_a_charge(self):
        acs = _paid(
            payment_id="OFFLINE_1", pay_way=None, payment_method="acs_cod"
        )
        boxnow = _paid(
            payment_id="OFFLINE_2", pay_way=None, payment_method="boxnow_cod"
        )

        _run()

        for order, provider in ((acs, "acs_cod"), (boxnow, "boxnow_cod")):
            [row] = _rows(order)
            assert row.kind == PaymentTransactionKind.COD_COLLECTION
            assert row.provider == provider
            # No courier voucher is read: the reference is the order's own.
            assert row.provider_reference == f"order-{order.pk}"
            assert row.amount == _eur("50.00")
        assert PaymentLedger.refundable(acs) == 0

    def test_the_payment_status_is_left_alone(self):
        order = _paid(payment_status=PaymentStatus.REFUNDED)

        _run()

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_running_it_again_adds_nothing(self):
        order = _paid(
            metadata={
                "refunds": [
                    {"refund_id": "re_1", "amount": "10.00", "reason": "x"}
                ]
            }
        )

        _run()
        _run()

        assert len(_rows(order)) == 2

    def test_two_orders_naming_one_payment_do_not_collide(self):
        # Orders from before ``order_payment_id_unique`` can still name one
        # payment (the duplicates that index now prevents), and 0067 has to
        # cope with them: it runs before the index exists on a real
        # database. The test database has it, so drop it, inside this
        # test's own transaction.
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX order_payment_id_unique")
        first = _paid(payment_id="pi_shared")
        second = _paid(payment_id="pi_shared")

        _run()

        assert len(_rows(first)) + len(_rows(second)) == 1
        assert (
            PaymentTransaction.objects.filter(
                provider_reference="pi_shared"
            ).count()
            == 1
        )


class TestRefunds:
    def test_a_refund_with_a_provider_id_is_a_row_against_the_charge(self):
        order = _paid(
            metadata={
                "refunds": [
                    {
                        "refund_id": "re_1",
                        "amount": "20.00",
                        "currency": "EUR",
                        "reason": "Order canceled: changed mind",
                        "refunded_at": "2026-05-01T10:00:00+00:00",
                        "refunded_by": 7,
                    }
                ]
            },
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        )

        _run()

        [refund] = _rows(order, REFUND)
        assert refund.provider == "stripe"
        assert refund.provider_reference == "re_1"
        assert refund.amount == _eur("20.00")
        assert refund.description == "Order canceled: changed mind"
        assert refund.charge == _rows(order, CHARGE)[0]
        # The moment it was refunded, not the moment it was backfilled.
        assert refund.created_at == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
        assert PaymentLedger.refundable(order) == Decimal("30.00")

    def test_a_viva_refund_entry_is_read_the_same_way(self):
        order = _paid(
            "viva_wallet",
            "viva-txn-9",
            "40.00",
            metadata={
                "refunds": [
                    {
                        "refund_id": "refund-txn-1",
                        "amount": "10.00",
                        "currency": "EUR",
                        "provider": "viva_wallet",
                        "refunded_at": "2026-10-10T09:00:00+00:00",
                    }
                ]
            },
        )

        _run()

        [refund] = _rows(order, REFUND)
        assert refund.provider == "viva_wallet"
        assert refund.provider_reference == "refund-txn-1"

    def test_a_refund_no_provider_id_was_kept_for_is_not_invented(self):
        """The four production Viva orders whose refund never reached Viva
        (#134): their money is still with the merchant, so the ledger shows
        the payment as refundable, which is true."""
        order = _paid(
            "viva_wallet",
            "viva-txn-34",
            "0.12",
            payment_status=PaymentStatus.REFUNDED,
            metadata={
                "refunds": [
                    {
                        "refund_id": None,
                        "amount": "0.12",
                        "currency": "EUR",
                        "reason": "Order canceled",
                    }
                ]
            },
        )

        _run()

        assert [r.kind for r in _rows(order)] == [CHARGE]
        assert PaymentLedger.refundable(order) == Decimal("0.12")

    def test_the_old_full_marker_means_whatever_the_payment_still_held(self):
        order = _paid(
            amount="50.00",
            payment_status=PaymentStatus.REFUNDED,
            metadata={"refunds": [{"refund_id": "re_full", "amount": "full"}]},
        )

        _run()

        [refund] = _rows(order, REFUND)
        assert refund.amount == _eur("50.00")
        assert PaymentLedger.refundable(order) == 0

    def test_stripe_webhook_totals_become_one_row_for_what_they_report(self):
        """``charge.refunded`` entries carry the charge's cumulative total
        (minor units) and no refund id: the latest one is the truth."""
        order = _paid(
            amount="50.00",
            payment_status=PaymentStatus.REFUNDED,
            metadata={
                "refunds": [
                    {
                        "stripe_event_id": "evt_1",
                        "amount_refunded": 1500,
                        "amount": 5000,
                        "currency": "eur",
                    },
                    {
                        "stripe_event_id": "evt_2",
                        "amount_refunded": 5000,
                        "amount": 5000,
                        "currency": "eur",
                    },
                ]
            },
        )

        _run()

        [refund] = _rows(order, REFUND)
        assert refund.provider_reference == "evt_2"
        assert refund.amount == _eur("50.00")

    def test_a_webhook_total_already_covered_by_named_refunds_adds_nothing(
        self,
    ):
        """The in-app refund and the webhook that followed it are two list
        entries for ONE refund."""
        order = _paid(
            amount="50.00",
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
            metadata={
                "refunds": [
                    {"refund_id": "re_1", "amount": "15.00"},
                    {
                        "stripe_event_id": "evt_1",
                        "amount_refunded": 1500,
                        "amount": 5000,
                        "currency": "eur",
                    },
                ]
            },
        )

        _run()

        assert [r.provider_reference for r in _rows(order, REFUND)] == ["re_1"]

    def test_a_webhook_total_beyond_the_named_refunds_adds_the_difference(self):
        order = _paid(
            amount="50.00",
            payment_status=PaymentStatus.REFUNDED,
            metadata={
                "refunds": [
                    {"refund_id": "re_1", "amount": "15.00"},
                    {
                        "stripe_event_id": "evt_dashboard",
                        "amount_refunded": 5000,
                        "amount": 5000,
                        "currency": "eur",
                    },
                ]
            },
        )

        _run()

        assert {
            r.provider_reference: r.amount.amount for r in _rows(order, REFUND)
        } == {"re_1": Decimal("15.00"), "evt_dashboard": Decimal("35.00")}
        assert PaymentLedger.refundable(order) == 0

    def test_a_zero_decimal_currency_total_is_not_divided_by_a_hundred(self):
        order = _paid(
            amount="5000.00",
            metadata={
                "refunds": [
                    {
                        "stripe_event_id": "evt_jpy",
                        "amount_refunded": 500,
                        "amount": 5000,
                        "currency": "jpy",
                    }
                ]
            },
        )

        _run()

        [refund] = _rows(order, REFUND)
        assert refund.amount.amount == Decimal("500.00")

    def test_an_unreadable_entry_is_skipped_not_fatal(self):
        order = _paid(
            metadata={
                "refunds": [
                    {"refund_id": "re_bad", "amount": "not a number"},
                    {"refund_id": "re_zero", "amount": "0.00"},
                    {
                        "stripe_event_id": "evt_x",
                        "amount_refunded": 100,
                        "currency": "zzz",
                    },
                    {"refund_id": "re_ok", "amount": "5.00"},
                ]
            }
        )

        _run()

        assert [r.provider_reference for r in _rows(order, REFUND)] == ["re_ok"]
