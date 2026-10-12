"""A customer cancel asks the courier first.

Between the moment a voucher exists and the next tracking poll that sees
the parcel move, an order is still PENDING / PROCESSING, so a cancel was
accepted and refunded while the courier had already taken the parcel. The
customer cancel now asks the courier to cancel the voucher before anything
else changes and only goes on when the courier confirms (or no voucher
exists). A refusal, or a courier that cannot be reached, leaves the order
exactly as it was; the answer says which of the two it was, because a
courier that gives no answer may give one later. Staff and system cancels
are not bound by the courier: they cancel and refund, and the refusal is
shown to staff.

Only the carrier HTTP boundary (``AcsClient`` / ``BoxNowClient``) and the
Viva HTTP boundary are faked.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from order.admin import OrderAdmin
from order.enum.cancel_error import OrderCancelErrorType
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    CourierUnreachableError,
    OrderCancellationError,
    ParcelWithCourierError,
)
from order.factories.order import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from shipping.models import ShippingProvider
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import (
    AcsAPIError,
    AcsAuthError,
    AcsRetryableError,
)
from shipping_acs.factories import AcsPickupListFactory, AcsShipmentFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowAuthError,
    BoxNowRetryableError,
)
from shipping_boxnow.factories import BoxNowShipmentFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.viva import FakeVivaApi
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

REFUSAL_DETAIL = (
    "Your parcel is already with the courier, so the order can no longer "
    "be canceled. Contact the store to arrange a return."
)
UNREACHABLE_DETAIL = (
    "We could not reach the courier to cancel your parcel. Please try "
    "again in a few minutes."
)


@dataclass(frozen=True)
class Outcome:
    """What the customer is told when the courier does not confirm."""

    status: int
    type: OrderCancelErrorType
    detail: str
    error: type[OrderCancellationError]


REFUSED = Outcome(
    status.HTTP_400_BAD_REQUEST,
    OrderCancelErrorType.PARCEL_WITH_COURIER,
    REFUSAL_DETAIL,
    ParcelWithCourierError,
)
UNREACHABLE = Outcome(
    status.HTTP_503_SERVICE_UNAVAILABLE,
    OrderCancelErrorType.COURIER_UNREACHABLE,
    UNREACHABLE_DETAIL,
    CourierUnreachableError,
)


class FakeAcs:
    """The ACS HTTP boundary: records the vouchers asked to be deleted."""

    def __init__(self, *, delete_error: Exception | None = None):
        self.delete_error = delete_error
        self.deleted: list[str] = []

    def __call__(self) -> FakeAcs:
        return self

    def delete_voucher(self, voucher_no: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(voucher_no)


@pytest.fixture
def acs(monkeypatch) -> FakeAcs:
    fake = FakeAcs()
    monkeypatch.setattr("shipping_acs.services.AcsClient", fake)
    return fake


@pytest.fixture
def boxnow():
    client = MagicMock()
    with patch("shipping_boxnow.services.BoxNowClient", client):
        yield client.return_value


def _paid_order(provider_code: str | None = None, **kwargs):
    """A paid Viva order the courier may already hold."""
    if provider_code is not None:
        kwargs["shipping_provider"] = ShippingProvider.objects.get(
            code=provider_code
        )
        kwargs["shipping_kind"] = (
            "pickup_point" if provider_code == "boxnow" else "home_delivery"
        )
    kwargs.setdefault(
        "pay_way", PayWayFactory.create_online_payment("viva_wallet")
    )
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    # ``Order.payment_id`` is unique: one per order.
    kwargs.setdefault("payment_id", str(uuid.uuid4()))
    kwargs.setdefault("paid_amount", Money(Decimal("21.10"), "EUR"))
    return OrderFactory(num_order_items=0, **kwargs)


def _acs_order(*, on_pickup_list: bool = False, **kwargs):
    order = _paid_order("acs", **kwargs)
    AcsShipmentFactory(
        order=order,
        with_voucher=True,
        pickup_list=AcsPickupListFactory() if on_pickup_list else None,
    )
    return order


def _boxnow_order(
    parcel_state: BoxNowParcelState = BoxNowParcelState.NEW, **kwargs
):
    order = _paid_order("boxnow", **kwargs)
    BoxNowShipmentFactory(order=order, with_parcel=True)
    shipment = order.boxnow_shipment
    shipment.parcel_state = parcel_state
    shipment.save(update_fields=["parcel_state"])
    return order


def _cancel(client: APIClient, order: Order):
    return client.post(
        reverse("order-cancel", kwargs={"pk": order.pk}),
        {"reason": "changed mind"},
        format="json",
    )


def _owner_client(order: Order) -> APIClient:
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=order.user)
    return client


@pytest.fixture
def staff_client():
    tenant = store_tenant("courier_cancel_tenant")
    staff = store_staff(tenant)
    previous = bind_store_tenant(tenant)
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=staff)
    yield client
    unbind_store_tenant(previous)


def _row(order: Order) -> dict:
    return (
        Order.objects.filter(pk=order.pk)
        .values("status", "payment_status", "metadata")
        .get()
    )


class TestACustomerCancelWithoutAVoucher:
    def test_cancels_and_refunds(self):
        user = UserAccountFactory()
        order = _paid_order(user=user)

        with FakeVivaApi() as viva:
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_200_OK, response.data
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        assert len(viva.deletes) == 1
        assert order.metadata["cancellation"]["shipment_cancel"] == {
            "attempted": True,
            "dispatched": False,
        }

    def test_a_carrier_without_a_parcel_row_is_not_asked(self, acs):
        user = UserAccountFactory()
        order = _paid_order("acs", user=user)

        with FakeVivaApi() as viva:
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_200_OK, response.data
        assert acs.deleted == []
        assert len(viva.deletes) == 1


class TestACustomerCancelTheCourierConfirms:
    def test_acs_voucher_is_canceled_first_then_the_order_is_refunded(
        self, acs
    ):
        user = UserAccountFactory()
        order = _acs_order(user=user)

        with FakeVivaApi() as viva:
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_200_OK, response.data
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        assert len(viva.deletes) == 1
        # Asked once: the cascade after the cancel does not repeat it.
        assert acs.deleted == [order.acs_shipment.voucher_no]
        assert order.acs_shipment.shipment_state == AcsShipmentState.CANCELED
        assert order.metadata["cancellation"]["shipment_cancel"] == {
            "attempted": True,
            "dispatched": True,
        }

    def test_boxnow_parcel_is_canceled_first_then_the_order_is_refunded(
        self, boxnow
    ):
        user = UserAccountFactory()
        order = _boxnow_order(user=user)

        with FakeVivaApi() as viva:
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_200_OK, response.data
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        assert len(viva.deletes) == 1
        boxnow.cancel_parcel.assert_called_once_with(
            order.boxnow_shipment.parcel_id
        )
        assert order.boxnow_shipment.parcel_state == BoxNowParcelState.CANCELED

    def test_a_retry_after_a_canceled_parcel_goes_through(self, boxnow):
        """The courier already canceled the parcel (an earlier attempt that
        stopped before the order moved): the parcel is as canceled as
        asked, so the cancel is not refused."""
        user = UserAccountFactory()
        order = _boxnow_order(BoxNowParcelState.CANCELED, user=user)

        with FakeVivaApi():
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_200_OK, response.data
        boxnow.cancel_parcel.assert_not_called()
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED


@pytest.mark.assert_english
class TestACustomerCancelTheCourierDoesNotConfirm:
    """Whatever stops the courier cancelling, the order stays untouched.

    A refusal (the courier judged the parcel and said no) is a 400 to
    take up with the store; no answer at all (unreachable, a 5xx, an
    auth-layer rejection) is a 503 the customer can retry."""

    CASES = {
        "acs_on_pickup_list": REFUSED,
        "acs_business_error": REFUSED,
        "acs_unreachable": UNREACHABLE,
        "acs_auth_layer": UNREACHABLE,
        "boxnow_in_depot": REFUSED,
        "boxnow_business_error": REFUSED,
        "boxnow_unreachable": UNREACHABLE,
        "boxnow_auth_layer": UNREACHABLE,
    }

    @pytest.fixture(params=list(CASES))
    def held(self, request, monkeypatch) -> tuple[Order, Outcome]:
        """An owned order the courier will not cancel, and the outcome."""
        user = UserAccountFactory()
        case = request.param
        if case.startswith("acs"):
            error = {
                "acs_business_error": AcsAPIError(
                    alias="ACS_Delete_Voucher",
                    error_message="Voucher already collected.",
                ),
                "acs_unreachable": AcsRetryableError(
                    alias="ACS_Delete_Voucher", error_message="HTTP 503"
                ),
                "acs_auth_layer": AcsAuthError(
                    alias="ACS_Delete_Voucher", http_status=406
                ),
            }.get(case)
            monkeypatch.setattr(
                "shipping_acs.services.AcsClient", FakeAcs(delete_error=error)
            )
            order = _acs_order(
                user=user, on_pickup_list=case == "acs_on_pickup_list"
            )
            return order, self.CASES[case]

        client = MagicMock()
        client.return_value.cancel_parcel.side_effect = {
            "boxnow_business_error": BoxNowAPIError(
                409, code="P420", message="Parcel not cancellable."
            ),
            "boxnow_unreachable": BoxNowRetryableError(
                503, message="Service unavailable"
            ),
            "boxnow_auth_layer": BoxNowAuthError(401, message="Unauthorized"),
        }.get(case)
        monkeypatch.setattr("shipping_boxnow.services.BoxNowClient", client)
        order = _boxnow_order(
            BoxNowParcelState.IN_DEPOT
            if case == "boxnow_in_depot"
            else BoxNowParcelState.NEW,
            user=user,
        )
        return order, self.CASES[case]

    def test_is_answered_with_a_code_and_a_message(self, held):
        order, outcome = held

        with FakeVivaApi():
            response = _cancel(_owner_client(order), order)

        assert response.status_code == outcome.status
        assert response.data["error"] == {"type": outcome.type}
        assert response.data["detail"] == outcome.detail

    def test_changes_nothing(self, held):
        order, _outcome = held
        shipment = (
            order.acs_shipment
            if order.shipping_provider.code == "acs"
            else order.boxnow_shipment
        )
        state_before = (
            getattr(shipment, "shipment_state", None) or shipment.parcel_state
        )
        order_before = _row(order)

        with FakeVivaApi() as viva:
            _cancel(_owner_client(order), order)

        assert _row(order) == order_before
        assert order_before["status"] == OrderStatus.PROCESSING
        assert order_before["payment_status"] == PaymentStatus.COMPLETED
        assert "cancellation" not in order_before["metadata"]
        # No refund call, and no shipment change.
        assert viva.sent == []
        shipment.refresh_from_db()
        assert (
            getattr(shipment, "shipment_state", None) or shipment.parcel_state
        ) == state_before
        assert not OrderHistory.objects.filter(
            order=order, change_type="STATUS"
        ).exists()

    def test_the_service_raises_the_typed_error(self, held):
        order, outcome = held

        with FakeVivaApi() as viva, pytest.raises(outcome.error):
            OrderService.cancel_order(order, reason="changed mind")

        assert viva.sent == []
        assert _row(order)["status"] == OrderStatus.PROCESSING

    def test_a_guest_holding_the_uuid_is_held_to_the_courier_too(self, acs):
        order = _acs_order(user=None, on_pickup_list=True)

        with FakeVivaApi() as viva:
            response = APIClient(HTTP_ACCEPT_LANGUAGE="en").post(
                reverse("order-cancel", kwargs={"pk": order.pk})
                + f"?uuid={order.uuid}",
                {},
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["type"] == (
            OrderCancelErrorType.PARCEL_WITH_COURIER
        )
        assert viva.sent == []
        assert _row(order)["status"] == OrderStatus.PROCESSING


@pytest.mark.assert_english
class TestAFaultOfOursIsNotTheCouriersAnswer:
    """Only a courier error is the courier's answer. Anything else that
    escapes the courier call is a bug of ours: a 500 that reaches the
    error log, never a refusal the customer is told to take up with the
    store."""

    @pytest.fixture
    def order(self, monkeypatch) -> Order:
        monkeypatch.setattr(
            "shipping_acs.services.AcsClient",
            FakeAcs(delete_error=KeyError("cod_amount")),
        )
        return _acs_order(user=UserAccountFactory())

    def test_is_a_server_error_and_changes_nothing(self, order):
        order_before = _row(order)

        with FakeVivaApi() as viva:
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "error" not in response.data
        assert _row(order) == order_before
        assert viva.sent == []

    def test_the_service_raises_it_as_itself(self, order):
        with FakeVivaApi(), pytest.raises(KeyError):
            OrderService.cancel_order(order, reason="changed mind")


@pytest.mark.assert_english
class TestTheOrderMovesOnWhileTheCourierAnswers:
    """The courier is asked before the order row is locked, so the order
    can move on while it answers. A parcel the courier canceled for an
    order that then does not cancel has no voucher behind it any more:
    staff are told to create a new one."""

    @staticmethod
    def _courier_moving_the_order(monkeypatch, order: Order, to: str):
        courier = FakeAcs()
        confirm = courier.delete_voucher

        def delete_voucher(voucher_no: str) -> None:
            Order.objects.filter(pk=order.pk).update(status=to)
            confirm(voucher_no)

        courier.delete_voucher = delete_voucher
        monkeypatch.setattr("shipping_acs.services.AcsClient", courier)
        return courier

    @staticmethod
    def _parcel_notes(order: Order) -> list[str]:
        return [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("The customer asked")
        ]

    def test_staff_are_told_the_order_needs_a_new_voucher(
        self, monkeypatch, caplog
    ):
        order = _acs_order(user=UserAccountFactory())
        courier = self._courier_moving_the_order(
            monkeypatch, order, OrderStatus.SHIPPED
        )

        with (
            FakeVivaApi() as viva,
            caplog.at_level(logging.ERROR, logger="order.services"),
        ):
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot be canceled" in response.data["detail"]
        assert courier.deleted == [order.acs_shipment.voucher_no]
        assert _row(order)["status"] == OrderStatus.SHIPPED
        assert viva.sent == []
        [note] = self._parcel_notes(order)
        assert "its status is SHIPPED" in note
        assert "create a new one" in note
        assert any(
            record.levelno == logging.ERROR
            and "needs a new voucher" in record.getMessage()
            for record in caplog.records
        )

    def test_an_order_canceled_meanwhile_needs_no_note(self, monkeypatch):
        order = _acs_order(user=UserAccountFactory())
        self._courier_moving_the_order(monkeypatch, order, OrderStatus.CANCELED)

        with FakeVivaApi():
            response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert self._parcel_notes(order) == []


class TestTheRefusalIsInTheRequestLanguage:
    """Not pinned to English: the point is the language the caller asked
    for. The expected text is computed with ``translation.override``
    rather than written out, because CI has no ``compilemessages`` step
    (see ``tests/integration/core/test_request_language.py``)."""

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_the_message_follows_the_request_and_the_code_does_not(
        self, acs, language
    ):
        order = _acs_order(user=UserAccountFactory(), on_pickup_list=True)
        client = APIClient()
        client.force_authenticate(user=order.user)

        with FakeVivaApi():
            response = client.post(
                reverse("order-cancel", kwargs={"pk": order.pk}),
                {},
                format="json",
                headers={"x-language": language},
            )

        with translation.override(language):
            expected = gettext(REFUSAL_DETAIL)
        assert response["Content-Language"] == language
        assert response.data["detail"] == expected
        assert response.data["error"]["type"] == (
            OrderCancelErrorType.PARCEL_WITH_COURIER
        )


@pytest.mark.assert_english
class TestAStatusThatNoLongerAllowsACancelNeverAsksTheCourier:
    def test_a_shipped_order_gets_the_usual_error_and_the_courier_is_left_alone(
        self, acs
    ):
        user = UserAccountFactory()
        order = _acs_order(user=user, status=OrderStatus.SHIPPED)

        response = _cancel(_owner_client(order), order)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" not in response.data
        assert "cannot be canceled" in response.data["detail"]
        assert acs.deleted == []


@pytest.mark.assert_english
class TestAStaffCancelIsNotBoundByTheCourier:
    def test_it_cancels_and_refunds_and_shows_the_refusal(
        self, acs, staff_client
    ):
        order = _acs_order(user=UserAccountFactory(), on_pickup_list=True)

        with FakeVivaApi() as viva:
            response = _cancel(staff_client, order)

        assert response.status_code == status.HTTP_200_OK, response.data
        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.payment_status == PaymentStatus.REFUNDED
        assert len(viva.deletes) == 1
        assert acs.deleted == []
        shipment_cancel = order.metadata["cancellation"]["shipment_cancel"]
        assert shipment_cancel["dispatched"] is False
        assert "already in a pickup list" in shipment_cancel["error"]

        [note] = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("Order canceled, but the")
        ]
        assert "already in a pickup list" in note
        assert "may still be delivered" in note

    def test_the_service_cancels_a_system_cancel_the_same_way(self, acs):
        order = _acs_order(user=None, on_pickup_list=True)

        with FakeVivaApi() as viva:
            canceled, refund_info = OrderService.cancel_order(
                order, reason="auto", customer_initiated=False
            )

        assert canceled.status == OrderStatus.CANCELED
        assert refund_info is not None
        assert refund_info["refunded"] is True
        assert len(viva.deletes) == 1

    def test_the_admin_action_reports_the_refusal(self, acs):
        order = _acs_order(user=UserAccountFactory(), on_pickup_list=True)
        request = RequestFactory().get("/")
        request.user = get_user_model()(
            id=1, username="staff", is_superuser=True
        )

        with (
            FakeVivaApi() as viva,
            patch.object(OrderAdmin, "message_user") as message,
        ):
            OrderAdmin(Order, AdminSite()).mark_as_canceled(
                request, Order.objects.filter(pk=order.pk)
            )

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert len(viva.deletes) == 1
        assert message.call_count == 2
        warning = message.call_args_list[-1]
        assert warning.kwargs == {"level": "error"}
        assert "did not cancel its parcel" in str(warning.args[1])
        assert "already in a pickup list" in str(warning.args[1])


class TestCanBeCanceled:
    """``canBeCanceled`` is the button's rule, read from local data."""

    def _flag(self, order: Order) -> bool:
        response = _owner_client(order).get(
            reverse("order-detail", kwargs={"pk": order.pk})
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data["can_be_canceled"]

    def test_true_with_no_shipment(self):
        assert self._flag(_paid_order(user=UserAccountFactory())) is True

    def test_true_for_an_acs_voucher_not_yet_on_a_pickup_list(self):
        assert self._flag(_acs_order(user=UserAccountFactory())) is True

    def test_false_once_the_acs_voucher_is_on_a_pickup_list(self):
        order = _acs_order(user=UserAccountFactory(), on_pickup_list=True)

        assert self._flag(order) is False

    def test_true_for_a_new_boxnow_parcel(self):
        assert self._flag(_boxnow_order(user=UserAccountFactory())) is True

    @pytest.mark.parametrize(
        "parcel_state",
        [
            BoxNowParcelState.IN_DEPOT,
            BoxNowParcelState.FINAL_DESTINATION,
            BoxNowParcelState.ACCEPTED_TO_LOCKER,
        ],
    )
    def test_false_once_the_boxnow_parcel_moved_on(self, parcel_state):
        order = _boxnow_order(parcel_state, user=UserAccountFactory())

        assert self._flag(order) is False

    def test_false_when_the_status_no_longer_allows_it(self):
        order = _acs_order(
            user=UserAccountFactory(), status=OrderStatus.SHIPPED
        )

        assert self._flag(order) is False

    def test_the_order_list_reads_it_without_a_query_per_order(self):
        user = UserAccountFactory(num_addresses=0)
        client = _owner_client(_paid_order(user=user))
        url = reverse("order-list")

        def make(count: int) -> None:
            for index in range(count):
                if index % 2:
                    _acs_order(user=user, on_pickup_list=True)
                else:
                    _boxnow_order(user=user)

        def queries() -> int:
            client.get(url)  # prime caches the factories clear
            with CaptureQueriesContext(connection) as ctx:
                response = client.get(url)
            assert response.status_code == status.HTTP_200_OK
            return len(ctx)

        make(2)
        before = queries()
        make(4)

        assert queries() == before

    def test_the_list_flag_follows_the_shipment(self):
        user = UserAccountFactory(num_addresses=0)
        open_order = _acs_order(user=user)
        held_order = _acs_order(user=user, on_pickup_list=True)

        response = _owner_client(open_order).get(reverse("order-list"))

        flags = {
            row["id"]: row["can_be_canceled"]
            for row in response.data["results"]
        }
        assert flags[open_order.id] is True
        assert flags[held_order.id] is False
