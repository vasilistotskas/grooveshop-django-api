"""Viva webhook reversal (1797) and failure (1798) handling (G0275, #135).

The Viva webhook endpoint is unauthenticated (no HMAC; the source-IP check
is non-blocking), so the reversal and failed handlers verify the event
against Viva's Retrieve Transaction API before mutating ``payment_status``
— a spoofed event must not be able to flip an order to REFUNDED/FAILED.

The 1797 tests run the dispatcher against Viva's DOCUMENTED payloads and
fake only the HTTP boundary (``tests/utils/viva.py``), never our own
verifier. They used to mock ``verify_transaction`` into answering
"REFUNDED" for the event's transaction, which is not what Viva returns: the
event's ``TransactionId`` is a refund transaction of its own with
``statusId`` ``F`` (``R`` belongs to the ORIGINAL payment), so a real
refund was never applied and the suite pinned that as intended.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.lifecycle.service import OrderLifecycleService
from order.models.history import OrderHistory
from order.models.order import AMOUNT_MISMATCH_FLAG, Order
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payments import payment_hold
from order.payments.viva_settlement import handle_payment_created
from order.refunds.service import RefundService
from order.signals import order_refunded
from order.views.viva_webhook import (
    _handle_payment_failed,
    _handle_reversal_created,
    _process_event_in_tenant,
)
from pay_way.factories import PayWayFactory
from tests.utils.viva import (
    CANCEL_SUCCESS,
    REVERSAL_CREATED_EVENT,
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
    reversal_created_event,
    verified_payment,
)

# The ids of Viva's documented 1797 sample: the refund, the payment it
# reverses, and the order they belong to.
REFUND_ID = REVERSAL_CREATED_EVENT["EventData"]["TransactionId"]
PAYMENT_ID = REVERSAL_CREATED_EVENT["EventData"]["ParentId"]
ORDER_CODE = REVERSAL_CREATED_EVENT["EventData"]["OrderCode"]
CAPTURED = 40.0


@pytest.fixture
def refunded_signals():
    """Every ``order_refunded`` the test caused."""
    received: list[dict] = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    order_refunded.connect(receiver, weak=False)
    yield received
    order_refunded.disconnect(receiver)


def _paid_order(*, ledger=True, **kwargs):
    """A paid Viva order. ``ledger=False`` leaves the capture unrecorded."""
    kwargs.setdefault(
        "pay_way", PayWayFactory.create_online_payment("viva_wallet")
    )
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", PAYMENT_ID)
    kwargs.setdefault("amount_due", Money(Decimal(str(CAPTURED)), "EUR"))
    # The codes this order actually issued: Viva's guidance is to confirm
    # a result with the COMBINATION of OrderCode and TransactionId.
    kwargs.setdefault("metadata", {"viva_order_codes": [str(ORDER_CODE)]})
    order = OrderFactory(num_order_items=0, **kwargs)
    if ledger:
        _charge(order, PAYMENT_ID)
    return order


def _charge(order, payment_id, amount=CAPTURED):
    """The ledger's record of a payment Viva captured for *order*."""
    return PaymentTransactionFactory(
        order=order,
        provider="viva_wallet",
        provider_reference=payment_id,
        amount=Money(Decimal(str(amount)), "EUR"),
    )


def _refund_rows(order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


def _payment_answer(**overrides):
    """Retrieve Transaction for the payment AFTER it was refunded: Viva
    reads ``R`` on the original, whose amount is still what was captured."""
    return (
        200,
        retrieve_transaction(
            **{
                "amount": CAPTURED,
                "orderCode": ORDER_CODE,
                "statusId": "R",
                **overrides,
            }
        ),
    )


def _viva(refund_amount=10.0, **refund_overrides):
    """A Viva in which ``REFUND_ID`` refunded ``refund_amount`` of the
    payment; ``refund_overrides`` bend what Retrieve says about it."""
    return FakeVivaApi(
        transactions={
            PAYMENT_ID: _payment_answer(),
            REFUND_ID: (
                200,
                retrieve_refund(
                    amount=refund_amount,
                    order_code=ORDER_CODE,
                    **refund_overrides,
                ),
            ),
        }
    )


def _deliver(*, refund_id=REFUND_ID, parent_id=PAYMENT_ID):
    """Hand the dispatcher the documented 1797 body for this refund."""
    body = reversal_created_event(
        refund_id=refund_id, parent_id=parent_id, order_code=ORDER_CODE
    )
    data = body["EventData"]
    return _process_event_in_tenant(
        order_code=str(data["OrderCode"]),
        event_type_id=body["EventTypeId"],
        event_data=data,
        transaction_id=data["TransactionId"],
        status_id=data["StatusId"],
        txn_hash="deadbeef",
    )


def _outcome(refund_id=REFUND_ID):
    return VivaWebhookEvent.objects.get(
        transaction_id=refund_id, event_type_id=1797
    ).outcome


def _assert_nothing_booked(order, refunded_signals, payment_status):
    order.refresh_from_db()
    assert order.payment_status == payment_status
    assert not _refund_rows(order).exists()
    assert refunded_signals == []


@pytest.mark.django_db
class TestVivaReversalWebhook:
    def test_a_documented_full_refund_refunds_the_order(self, refunded_signals):
        order = _paid_order()

        with _viva(refund_amount=40.0) as fake:
            response = _deliver()

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        # Whether a refund also means the goods came back is the admin's
        # call: the status of the order is left alone.
        assert order.status == OrderStatus.PROCESSING
        [refund] = _refund_rows(order)
        assert refund.provider_reference == REFUND_ID
        assert refund.provider == "viva_wallet"
        assert refund.amount == Money(Decimal(40), "EUR")
        assert refund.charge.provider_reference == PAYMENT_ID
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal(40), "EUR")
        ]
        assert _outcome() == VivaWebhookEvent.OUTCOME_PROCESSED
        # The refund is verified; what was captured is the ledger's to say.
        assert fake.retrieved == [REFUND_ID]
        row = OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).latest("id")
        assert row.new_value["payment_status"] == PaymentStatus.REFUNDED
        assert row.new_value["refund_id"] == REFUND_ID

    def test_a_part_refund_is_partially_refunded_and_carries_its_amount(
        self, refunded_signals
    ):
        order = _paid_order()

        # The documented body says -1000 (it contradicts its own
        # OriginalAmount of -12); Retrieve Transaction says 10 euros, and
        # is the only one believed.
        with _viva(refund_amount=10.0):
            response = _deliver()

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        [refund] = _refund_rows(order)
        assert refund.amount == Money(Decimal(10), "EUR")
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal(10), "EUR")
        ]
        assert _outcome() == VivaWebhookEvent.OUTCOME_PROCESSED

    def test_refunds_that_add_up_to_the_captured_amount_refund_the_order(
        self, refunded_signals
    ):
        order = _paid_order()
        second_refund = "7c1fd5ab-5f4e-4b6c-a3a2-5d0e9f7b1c11"
        viva = FakeVivaApi(
            transactions={
                PAYMENT_ID: _payment_answer(),
                REFUND_ID: (
                    200,
                    retrieve_refund(amount=10.0, order_code=ORDER_CODE),
                ),
                second_refund: (
                    200,
                    retrieve_refund(
                        amount=30.0, order_code=ORDER_CODE, type_id=4
                    ),
                ),
            }
        )

        with viva:
            _deliver()
            order.refresh_from_db()
            assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
            _deliver(refund_id=second_refund)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [r.provider_reference for r in _refund_rows(order)] == [
            REFUND_ID,
            second_refund,
        ]
        assert [r["amount"] for r in refunded_signals] == [
            Money(Decimal(10), "EUR"),
            Money(Decimal(30), "EUR"),
        ]

    def test_a_redelivered_event_is_applied_once(self, refunded_signals):
        order = _paid_order()

        with _viva(refund_amount=10.0):
            _deliver()
            response = _deliver()

        assert response.status_code == 200
        assert _refund_rows(order).count() == 1
        assert len(refunded_signals) == 1
        assert (
            VivaWebhookEvent.objects.filter(
                transaction_id=REFUND_ID, event_type_id=1797
            ).count()
            == 1
        )

    def test_the_ledger_absorbs_a_delivery_the_event_log_did_not_stop(
        self, refunded_signals
    ):
        """``VivaWebhookEvent`` is the first guard; the ledger's unique
        ``(provider, provider_reference, kind)`` is the one that holds when
        a delivery gets past it (a parallel request, a wiped event log)."""
        order = _paid_order()
        data = reversal_created_event(
            refund_id=REFUND_ID, parent_id=PAYMENT_ID, order_code=ORDER_CODE
        )["EventData"]

        with _viva(refund_amount=10.0):
            first = _handle_reversal_created(order, data, REFUND_ID)
            order.refresh_from_db()
            second = _handle_reversal_created(order, data, REFUND_ID)

        assert first is None  # booked
        assert second == VivaWebhookEvent.OUTCOME_SKIPPED
        assert _refund_rows(order).count() == 1
        assert len(refunded_signals) == 1
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_the_refund_the_app_made_is_not_applied_again_by_its_event(
        self, refunded_signals
    ):
        """Viva notifies EVERY refund, including the one ``cancel_order``
        just asked it for; booking that event too would refund the order
        twice (two history rows, two Meta events)."""
        order = _paid_order()
        in_app_refund = CANCEL_SUCCESS["TransactionId"]
        viva = FakeVivaApi(
            captured=CAPTURED,
            transactions={
                in_app_refund: (
                    200,
                    retrieve_refund(amount=CAPTURED, order_code=ORDER_CODE),
                ),
            },
        )

        with viva:
            OrderLifecycleService.cancel_order(order, reason="changed mind")
            assert len(refunded_signals) == 1
            _deliver(refund_id=in_app_refund)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert [r.provider_reference for r in _refund_rows(order)] == [
            in_app_refund
        ]
        assert len(refunded_signals) == 1
        assert _outcome(in_app_refund) == VivaWebhookEvent.OUTCOME_SKIPPED

    def test_a_part_refund_the_app_made_is_not_counted_twice(
        self, refunded_signals
    ):
        """The order is only PARTIALLY_REFUNDED after it, so nothing but
        the refund id tells the event that follows from one already
        booked: counting it again would add the EUR 10 up to EUR 20."""
        order = _paid_order()
        in_app_refund = CANCEL_SUCCESS["TransactionId"]
        viva = FakeVivaApi(
            captured=CAPTURED,
            transactions={
                in_app_refund: (
                    200,
                    retrieve_refund(amount=10.0, order_code=ORDER_CODE),
                ),
            },
        )

        with viva:
            RefundService.refund_order(
                order, amount=Money(Decimal("10.00"), "EUR")
            )
            _deliver(refund_id=in_app_refund)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert [r.amount.amount for r in _refund_rows(order)] == [
            Decimal("10.00")
        ]
        assert len(refunded_signals) == 1
        assert _outcome(in_app_refund) == VivaWebhookEvent.OUTCOME_SKIPPED

    def test_reversal_skipped_when_transaction_not_refunded(
        self, refunded_signals
    ):
        """A spoofed 1797 quoting the id of a payment that was never
        refunded (Viva's documented Retrieve example: ``F``, type 5, a
        positive amount) is not honoured."""
        order = _paid_order()

        # ``refund_amount`` is what was paid back, so a payment's positive
        # 21.10 is a refund of -21.10.
        with _viva(refund_amount=-21.1, transactionTypeId=5):
            response = _deliver()

        assert response.status_code == 200
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)
        assert _outcome() == VivaWebhookEvent.OUTCOME_SKIPPED

    @pytest.mark.parametrize(
        "retrieved",
        [
            pytest.param({"statusId": "E"}, id="a-refund-that-failed"),
            pytest.param({"refund_amount": -10.0}, id="a-positive-amount"),
            pytest.param(
                {"orderCode": 1234567890123456}, id="another-orders-refund"
            ),
        ],
    )
    def test_reversal_skipped_for_anything_but_a_refund_of_this_order(
        self, refunded_signals, retrieved
    ):
        order = _paid_order()

        with _viva(**retrieved):
            response = _deliver()

        assert response.status_code == 200
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)
        assert _outcome() == VivaWebhookEvent.OUTCOME_SKIPPED

    def test_reversal_skipped_when_it_reverses_another_payment(
        self, refunded_signals
    ):
        order = _paid_order()

        with _viva():
            response = _deliver(
                parent_id="0f5d3a52-7a2b-4e1c-9a53-2b7c8e0d4f66"
            )

        assert response.status_code == 200
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)
        # A parent that is none of the order's payments records nothing:
        # Viva's own event for this refund is still applied.
        assert not VivaWebhookEvent.objects.exists()

    def test_a_refund_is_attached_to_the_payment_its_parent_names(
        self, refunded_signals
    ):
        """An order paid twice (two of its checkout codes) holds two
        charges; the refund reverses the one ``ParentId`` names."""
        order = _paid_order()
        second_payment = "5d1f7b84-0b51-4d31-8f0e-6c9a3d2e7a10"
        _charge(order, second_payment, amount=25)

        with FakeVivaApi(
            transactions={
                REFUND_ID: (
                    200,
                    retrieve_refund(amount=25.0, order_code=ORDER_CODE),
                ),
            }
        ):
            _deliver(parent_id=second_payment)

        [refund] = _refund_rows(order)
        assert refund.charge.provider_reference == second_payment
        order.refresh_from_db()
        # 25 of the 65 captured across both payments is back.
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_a_payment_the_ledger_never_recorded_is_read_from_viva(
        self, refunded_signals
    ):
        """The order's own ``payment_id`` is trusted (the event's
        ``ParentId`` must equal it); the capture is read from Viva once and
        recorded, and the refund is booked against it."""
        order = _paid_order(ledger=False)

        with _viva(refund_amount=10.0) as fake:
            _deliver()

        assert fake.retrieved == [REFUND_ID, PAYMENT_ID]
        [charge] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGE
        )
        assert charge.provider_reference == PAYMENT_ID
        assert charge.amount == Money(Decimal(str(CAPTURED)), "EUR")
        [refund] = _refund_rows(order)
        assert refund.charge == charge
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_a_refund_before_its_payment_was_booked_books_that_payment(
        self, refunded_signals
    ):
        """A refund whose payment no webhook booked yet (its 1796 is late,
        lost, or verified and waiting for the order's lock) is not dropped:
        Viva says the payment is this order's, so it is booked first and
        the refund against it. The order, never confirmed and now holding
        nothing, is canceled and its stock released. The payment's own
        event, arriving after, neither revives nor ships it."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        with _viva(refund_amount=CAPTURED) as fake:
            response = _deliver()

        assert response.status_code == 200
        assert fake.retrieved == [REFUND_ID, PAYMENT_ID]
        [refund] = _refund_rows(order)
        assert refund.charge.provider_reference == PAYMENT_ID
        assert refund.charge.amount == Money(Decimal(str(CAPTURED)), "EUR")
        assert _outcome() == VivaWebhookEvent.OUTCOME_PROCESSED
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert order.status == OrderStatus.CANCELED

        with patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ) as dispatch:
            handle_payment_created(
                order,
                {"StatusId": "F"},
                PAYMENT_ID,
                verified=(
                    PaymentStatus.COMPLETED,
                    verified_payment(order, str(ORDER_CODE)),
                ),
            )

        dispatch.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_a_part_refund_before_its_payment_was_booked_is_flagged(
        self, refunded_signals
    ):
        """What a part refund leaves held belongs to an order that was never
        confirmed: it is flagged for the store as a charge that does not
        match the order (``flag_payment_mismatch``: the flag, a history
        note and the staff email), never left PENDING unnoticed."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

        with _viva(refund_amount=10.0), patch("emails.send.send_email") as sent:
            _deliver()

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        flag = order.metadata[AMOUNT_MISMATCH_FLAG]
        assert flag["transaction_id"] == PAYMENT_ID
        assert flag["verified_amount"] == str(
            Money(Decimal(str(CAPTURED - 10.0)), "EUR")
        )
        assert "ops.payment_mismatch" in [
            c.args[0] for c in sent.call_args_list
        ]
        assert payment_hold.awaits_payment_review(order)

    def test_a_parent_in_other_letter_case_is_one_charge(
        self, refunded_signals
    ):
        """``ParentId`` comes from the unauthenticated body: a payment it
        names in other letters is the same Viva payment, and the 1796 that
        follows books no second CHARGE for it."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        viva = _viva(refund_amount=10.0)
        viva.transactions[PAYMENT_ID.upper()] = _payment_answer()

        with viva, patch("emails.send.send_email"):
            _deliver(parent_id=PAYMENT_ID.upper())
            order.refresh_from_db()
            handle_payment_created(
                order,
                {"StatusId": "F"},
                PAYMENT_ID,
                verified=(
                    PaymentStatus.COMPLETED,
                    verified_payment(order, str(ORDER_CODE)),
                ),
            )

        assert (
            order.payment_transactions.filter(
                kind=PaymentTransactionKind.CHARGE
            ).count()
            == 1
        )

    def test_a_parent_that_is_itself_a_refund_books_nothing(
        self, refunded_signals
    ):
        """A ``ParentId`` naming another refund of the order (a negative
        amount) is no payment: the event is skipped, never a 500 from the
        ledger's positive-amount constraint."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        other_refund = "7b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"
        viva = _viva(refund_amount=10.0)
        viva.transactions[other_refund] = (
            200,
            retrieve_refund(amount=5.0, order_code=ORDER_CODE),
        )

        with viva:
            response = _deliver(parent_id=other_refund)

        assert response.status_code == 200
        assert not order.payment_transactions.exists()
        assert not VivaWebhookEvent.objects.exists()

    @pytest.mark.parametrize("event_type_id", [1796, 1797, 1798, 1799])
    def test_viva_is_read_before_the_order_row_is_locked(
        self, event_type_id, refunded_signals
    ):
        """Every request an event sends to Viva (the refund and the payment
        it reverses, for a 1797) is sent before the order row is locked:
        a slow Viva never holds the row. Counted on the HTTP boundary, so a
        call by any path is seen."""
        _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        body = reversal_created_event(
            refund_id=REFUND_ID, parent_id=PAYMENT_ID, order_code=ORDER_CODE
        )["EventData"]
        sent_at_lock: list[int] = []
        lock = Order.objects.select_for_update
        viva = _viva(refund_amount=10.0)

        def _lock(*args, **kwargs):
            sent_at_lock.append(len(viva.sent))
            return lock(*args, **kwargs)

        with (
            viva,
            patch.object(Order.objects, "select_for_update", _lock),
            patch("emails.send.send_email"),
        ):
            _process_event_in_tenant(
                order_code=str(ORDER_CODE),
                event_type_id=event_type_id,
                event_data={**body, "StatusId": "F"},
                transaction_id=REFUND_ID,
                status_id="F",
                txn_hash="deadbeef",
            )

        assert sent_at_lock
        assert sent_at_lock[0] > 0
        assert len(viva.sent) == sent_at_lock[0]
        if event_type_id == 1797:
            assert viva.retrieved == [REFUND_ID, PAYMENT_ID]
            assert _refund_rows(
                Order.objects.get()
            ).get().charge.provider_reference == (PAYMENT_ID)

    def test_a_null_status_is_read_before_the_order_row_is_locked(
        self, refunded_signals
    ):
        """A 1796 whose ``StatusId`` is JSON ``null`` names no status, as
        an absent one: it is read before the lock like one that says ``F``,
        never from under the lock."""
        _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        body = reversal_created_event(
            refund_id=REFUND_ID, parent_id=PAYMENT_ID, order_code=ORDER_CODE
        )["EventData"]
        sent_at_lock: list[int] = []
        lock = Order.objects.select_for_update
        viva = _viva(refund_amount=10.0)

        def _lock(*args, **kwargs):
            sent_at_lock.append(len(viva.sent))
            return lock(*args, **kwargs)

        with (
            viva,
            patch.object(Order.objects, "select_for_update", _lock),
            patch("emails.send.send_email"),
        ):
            _process_event_in_tenant(
                order_code=str(ORDER_CODE),
                event_type_id=1796,
                event_data={**body, "StatusId": None},
                transaction_id=REFUND_ID,
                status_id="",
                txn_hash="deadbeef",
            )

        assert sent_at_lock
        assert sent_at_lock[0] > 0
        assert len(viva.sent) == sent_at_lock[0]

    def test_a_parent_is_not_read_for_a_refund_of_another_order(
        self, refunded_signals
    ):
        """The ``ParentId`` of the unauthenticated body is read from Viva
        only once the refund itself is one of this order's: another
        order's transaction gets one Retrieve and is skipped."""
        _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        viva = _viva()
        viva.transactions[REFUND_ID] = (
            200,
            retrieve_refund(amount=10.0, order_code=9999999999999999),
        )

        with viva:
            response = _deliver()

        assert response.status_code == 200
        assert viva.retrieved == [REFUND_ID]
        assert _outcome() == VivaWebhookEvent.OUTCOME_SKIPPED

    @pytest.mark.parametrize(
        ("parent_id", "answer"),
        [
            ("../orders?x=1", None),
            ("not-a-transaction", None),
            (
                "0f5d3a52-7a2b-4e1c-9a53-2b7c8e0d4f66",
                (404, ["Transaction Id does not exist."]),
            ),
        ],
        ids=["path", "not-a-guid", "unknown-to-viva"],
    )
    def test_a_forged_parent_is_skipped_never_a_500(
        self, parent_id, answer, refunded_signals
    ):
        """A ``ParentId`` that is no Viva transaction id is never sent to
        Viva, and one Viva does not know is a payment of nobody's: the
        event is skipped, not answered 500 for Viva to retry."""
        _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        viva = _viva()
        if answer is not None:
            viva.transactions[parent_id] = answer

        with viva:
            response = _deliver(parent_id=parent_id)

        assert response.status_code == 200
        expected = [REFUND_ID] if answer is None else [REFUND_ID, parent_id]
        assert viva.retrieved == expected
        # Nothing is recorded: Viva's own event for this refund, naming its
        # real parent, is still applied.
        assert not VivaWebhookEvent.objects.filter(
            transaction_id=REFUND_ID
        ).exists()
        with _viva(), patch("emails.send.send_email"):
            _deliver()
        assert _refund_rows(Order.objects.get()).exists()

    def test_a_transaction_id_is_one_segment_of_the_retrieve_url(self):
        """A ``ParentId`` from the body is sent to Viva as one path segment:
        whatever it holds cannot reach another Viva endpoint with the
        store's token."""
        from order.payments.payment import VivaWalletPaymentProvider

        with FakeVivaApi() as viva:
            VivaWalletPaymentProvider().get_payment_status("x/../orders?y=1")

        [retrieve] = [r for r in viva.sent if r.method == "GET"]
        assert retrieve.path_url == (
            "/checkout/v2/transactions/x%2F..%2Forders%3Fy%3D1"
        )

    def test_a_full_refund_of_a_charge_held_for_review_is_left_to_staff(
        self, refunded_signals
    ):
        """A charge that did not match the order is flagged and waits for
        the store. The store refunds it in full at Viva: the refund is
        booked, and the order stays on the review worklist for staff to
        close; an order awaiting review is never canceled automatically."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        owed = order.calculate_order_total_amount().amount
        with patch("emails.send.send_email"):
            handle_payment_created(
                order,
                {"StatusId": "F"},
                PAYMENT_ID,
                verified=(
                    PaymentStatus.COMPLETED,
                    verified_payment(
                        order, str(ORDER_CODE), amount=float(owed) + 10
                    ),
                ),
            )
            with _viva(refund_amount=CAPTURED):
                _deliver()

        order.refresh_from_db()
        assert _refund_rows(order).exists()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.REFUNDED
        assert payment_hold.awaits_payment_review(order)

    def test_a_part_refund_of_a_charge_held_for_review_keeps_its_flag(
        self, refunded_signals
    ):
        """A charge for the wrong amount is flagged by its 1796 and never
        booked. The store refunds its excess at Viva: the order stays on the
        review worklist with the flag it had, and staff are not emailed
        about the same payment a second time."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        owed = order.calculate_order_total_amount().amount

        with patch("emails.send.send_email") as sent:
            handle_payment_created(
                order,
                {"StatusId": "F"},
                PAYMENT_ID,
                verified=(
                    PaymentStatus.COMPLETED,
                    verified_payment(
                        order, str(ORDER_CODE), amount=float(owed) + 10
                    ),
                ),
            )
            order.refresh_from_db()
            flag = order.metadata[AMOUNT_MISMATCH_FLAG]

            with _viva(refund_amount=10.0):
                _deliver()

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert order.metadata[AMOUNT_MISMATCH_FLAG] == flag
        assert [c.args[0] for c in sent.call_args_list].count(
            "ops.payment_mismatch"
        ) == 1
        assert payment_hold.awaits_payment_review(order)

    def test_a_full_refund_leaves_an_order_flagged_for_another_payment(
        self, refunded_signals
    ):
        """Paid twice: payment A for the wrong amount is flagged and never
        booked, payment B's own event is lost. The store refunds B in full:
        the ledger holds nothing, yet A is still at Viva, so the order is not
        canceled and stays on the review worklist for A."""
        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        owed = order.calculate_order_total_amount().amount
        second = "5d1f7b84-0b51-4d31-8f0e-6c9a3d2e7a10"
        with patch("emails.send.send_email"):
            handle_payment_created(
                order,
                {"StatusId": "F"},
                PAYMENT_ID,
                verified=(
                    PaymentStatus.COMPLETED,
                    verified_payment(
                        order, str(ORDER_CODE), amount=float(owed) + 10
                    ),
                ),
            )
            viva = _viva(refund_amount=CAPTURED)
            viva.transactions[second] = _payment_answer()

            with viva:
                _deliver(parent_id=second)

        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING
        assert order.metadata[AMOUNT_MISMATCH_FLAG]["transaction_id"] == (
            PAYMENT_ID
        )
        assert payment_hold.awaits_payment_review(order)

    def test_a_full_card_refund_of_an_order_also_paid_by_gift_card_cancels_it(
        self, refunded_signals
    ):
        """Paid partly with a gift card, the order is PARTIALLY_REFUNDED once
        its whole card payment is refunded, yet the store holds none of the
        customer's money: it is canceled, and the cancel gives the gift card
        back."""
        from giftcard.factories import GiftCardFactory
        from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
        from loyalty.enum import TransactionType
        from loyalty.models.transaction import PointsTransaction

        order = _paid_order(
            ledger=False,
            payment_id="",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            gift_card_amount=Money(Decimal(5), "EUR"),
            loyalty_discount=Money(Decimal(3), "EUR"),
        )
        GiftCardTransaction.objects.create(
            gift_card=GiftCardFactory(initial_value=Money(Decimal(5), "EUR")),
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(-5),
            order=order,
        )
        PointsTransaction.objects.create(
            user=order.user,
            points=-300,
            transaction_type=TransactionType.REDEEM,
            reference_order=order,
        )

        with _viva(refund_amount=CAPTURED), patch("emails.send.send_email"):
            _deliver()

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        ).exists()
        assert PointsTransaction.objects.filter(
            reference_order=order,
            transaction_type=TransactionType.ADJUST,
            points__gt=0,
        ).exists()

    def test_reversal_skipped_when_its_parent_is_another_orders_payment(
        self, refunded_signals
    ):
        """The parent the ledger does not hold is booked only when Viva
        says it was paid on one of this order's codes."""
        order = _paid_order(ledger=False, payment_id="")
        viva = _viva()
        viva.transactions[PAYMENT_ID] = _payment_answer(
            orderCode=9999999999999999
        )

        with viva:
            response = _deliver()

        assert response.status_code == 200
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)
        assert not order.payment_transactions.exists()
        # A parent that is none of the order's payments records nothing:
        # Viva's own event for this refund is still applied.
        assert not VivaWebhookEvent.objects.exists()

    def test_reversal_skipped_when_no_transaction_id(self, refunded_signals):
        order = _paid_order()

        with FakeVivaApi() as fake:
            outcome = _handle_reversal_created(order, {}, "")

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
        assert fake.retrieved == []
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)

    def test_reversal_is_retried_when_verification_is_unavailable(
        self, refunded_signals
    ):
        """A forged/unknown transaction id (Viva answers 404) must answer
        500 so Viva retries and the tenant loop moves on — never a
        silent state flip, and no audit row to dedup the retry."""
        order = _paid_order()

        with FakeVivaApi(retrieve=(404, ["Transaction Id does not exist."])):
            response = _deliver(refund_id="forged-txn")

        assert response.status_code == 500
        assert not VivaWebhookEvent.objects.exists()
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)

    def test_reversal_is_retried_when_the_capture_cannot_be_recorded(
        self, refunded_signals
    ):
        """For an order the ledger holds no payment of, a refund cannot be
        attached to one without what Viva captured, so the event is retried
        rather than guessed at."""
        order = _paid_order(ledger=False)
        viva = FakeVivaApi(
            transactions={
                PAYMENT_ID: (404, ["Transaction Id does not exist."]),
                REFUND_ID: (
                    200,
                    retrieve_refund(amount=10.0, order_code=ORDER_CODE),
                ),
            }
        )

        with viva:
            response = _deliver()

        assert response.status_code == 500
        assert not VivaWebhookEvent.objects.exists()
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)


@pytest.mark.django_db
class TestVivaPaymentFailedVerification:
    def _order(self):
        # See the note above: the order must have issued the code its
        # verified transaction reports.
        return OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            payment_id="viva_txn_2",
            metadata={"viva_order_codes": ["OC2"]},
        )

    def test_failed_flips_to_failed_when_verified(
        self, django_capture_on_commit_callbacks
    ):
        """A verified failure is recorded and emails no one: a card order is
        announced only once its payment goes through."""
        order = self._order()
        with (
            patch(
                "order.payments.viva_settlement.verify_transaction",
                return_value=(PaymentStatus.FAILED, {"order_code": "OC2"}),
            ),
            patch("emails.send.send_email") as sent,
            django_capture_on_commit_callbacks(execute=True),
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        sent.assert_not_called()

    def test_failed_raises_when_verification_returns_error(self):
        """Viva failing to answer the verification is unavailable (raise),
        never a confirmed failure."""
        order = self._order()
        with (
            FakeVivaApi(retrieve=(503, "Service Unavailable")),
            pytest.raises(RuntimeError),
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_failed_skipped_when_transaction_still_completed(self):
        order = self._order()
        with patch(
            "order.payments.viva_settlement.verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"order_code": "OC2"}),
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_a_failed_event_that_changes_nothing_is_recorded_as_skipped(self):
        """The dispatcher used to ignore the handler's answer and stamp
        every 1798 ``processed``, whatever the handler did."""
        retrieved = retrieve_transaction()  # Viva's documented ``F`` payment
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            metadata={"viva_order_codes": [str(retrieved["orderCode"])]},
        )

        with FakeVivaApi(retrieve=(200, retrieved)):
            response = _process_event_in_tenant(
                order_code=str(retrieved["orderCode"]),
                event_type_id=1798,
                event_data={"StatusId": "E"},
                transaction_id="failed-txn",
                status_id="E",
                txn_hash="deadbeef",
            )

        assert response.status_code == 200
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert (
            VivaWebhookEvent.objects.get(transaction_id="failed-txn").outcome
            == VivaWebhookEvent.OUTCOME_SKIPPED
        )


@pytest.mark.django_db
class TestVivaPaymentCreatedVerification:
    """The payment-created (1796) handler must treat a Retrieve-
    Transaction ERROR as "unavailable" (raise → 500 → retry), not as a
    confirmed non-completion. This is both the fix for a latent
    single-tenant bug (a transient Viva error was silently acked) and
    the mechanism that lets a webhook whose transaction is not in THIS
    tenant's Viva account fall through to the next candidate tenant.
    """

    def _order(self):
        return OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=1,
            metadata={},
        )

    def test_raises_when_verification_reports_viva_error(self):
        """A 404 because the transaction is not in this tenant's Viva
        account must raise, not skip."""
        order = self._order()
        with (
            FakeVivaApi(retrieve=(404, "Not Found")),
            pytest.raises(RuntimeError),
        ):
            handle_payment_created(order, {"StatusId": "F"}, "foreign_txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_raises_when_verification_reports_error(self):
        order = self._order()
        with (
            FakeVivaApi(retrieve=(503, "Service Unavailable")),
            pytest.raises(RuntimeError),
        ):
            handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_clean_non_completed_status_skips_without_raising(self):
        """A genuine, error-free non-COMPLETED status (a real pending
        transaction in this account) is a legitimate skip — not a
        verification failure — so it must NOT raise."""
        order = self._order()
        with patch(
            "order.payments.viva_settlement.verify_transaction",
            return_value=(
                PaymentStatus.PENDING,
                {"order_code": "OC", "amount": None},
            ),
        ):
            # Does not raise.
            handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
