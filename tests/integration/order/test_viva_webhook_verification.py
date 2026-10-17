"""Viva webhook reversal (1797) and failure (1798) handling (G0275, #135).

The Viva webhook endpoint is unauthenticated (no HMAC; the source-IP check
is non-blocking), so the reversal and failed handlers verify the event
against Viva's Retrieve Transaction API before mutating ``payment_status``
— a spoofed event must not be able to flip an order to REFUNDED/FAILED.

The 1797 tests run the dispatcher against Viva's DOCUMENTED payloads and
fake only the HTTP boundary (``tests/utils/viva.py``), never our own
verifier. They used to mock ``_verify_transaction`` into answering
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
from order.models.history import OrderHistory
from order.models.viva_webhook_event import VivaWebhookEvent
from order.services import OrderService
from order.signals import order_refunded
from order.views.viva_webhook import (
    _handle_payment_created,
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
            OrderService.cancel_order(order, reason="changed mind")
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
            OrderService.refund_order(
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
        assert _outcome() == VivaWebhookEvent.OUTCOME_SKIPPED

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

    def test_reversal_skipped_when_the_ledger_holds_no_such_payment(
        self, refunded_signals
    ):
        """No ledger payment matches ``ParentId`` and it is not the order's
        own ``payment_id`` either: nothing to attach the refund to."""
        order = _paid_order(ledger=False, payment_id="")

        with _viva():
            response = _deliver()

        assert response.status_code == 200
        _assert_nothing_booked(order, refunded_signals, PaymentStatus.COMPLETED)
        assert _outcome() == VivaWebhookEvent.OUTCOME_SKIPPED

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

    def test_failed_flips_to_failed_when_verified(self):
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"order_code": "OC2"}),
            ),
            patch(
                "order.views.viva_webhook.send_payment_failed_email.apply_async"
            ) as mock_email,
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.FAILED
        # Dispatched with the schema captured at lambda-build time so
        # the worker enters the owning tenant's schema.
        mock_email.assert_called_once()
        assert mock_email.call_args.kwargs["args"] == [order.id]
        assert "_schema_name" in mock_email.call_args.kwargs["headers"]

    def test_failed_raises_when_verification_returns_error(self):
        """A network blip during verification maps to FAILED with an
        ``error`` key — that must be treated as unavailable (raise), not as
        a confirmed failure."""
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"error": "timeout"}),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_payment_failed(order, {}, "viva_txn_2")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_failed_skipped_when_transaction_still_completed(self):
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
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
        account maps to (FAILED, {viva_error: True}) — must raise, not
        skip."""
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"viva_error": True}),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "foreign_txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_raises_when_verification_reports_error(self):
        order = self._order()
        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(PaymentStatus.FAILED, {"error": "timeout"}),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_clean_non_completed_status_skips_without_raising(self):
        """A genuine, error-free non-COMPLETED status (a real pending
        transaction in this account) is a legitimate skip — not a
        verification failure — so it must NOT raise."""
        order = self._order()
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.PENDING,
                {"order_code": "OC", "amount": None},
            ),
        ):
            # Does not raise.
            _handle_payment_created(order, {"StatusId": "F"}, "txn")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
