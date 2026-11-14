"""A parcel BoxNow holds while the shipment has no parcel number (#606).

The creation call can reach BoxNow and lose its answer (a read timeout, a
5xx from a proxy). The row then has no ``parcel_id``, while BoxNow holds a
delivery request under the order's ``orderNumber``:

- the retry sends the same ``orderNumber`` and BoxNow answers P410 ("Order
  number conflict", API Manual v7.2, §5, p. 21). The parcel is looked up by
  ``orderNumber`` (``GET /api/v1/parcels?orderNumber=``, §6.5.3, p. 45) and
  taken over, instead of reported as a refusal;
- a cancel of that shipment asks BoxNow the same question and cancels the
  parcel it finds by id (``POST /api/v1/parcels/{id}:cancel``, §3.7, p.
  15). A lookup that fails refuses the cancel; it never counts as "nothing
  there".

A shipment whose creation was never sent (an unpaid order) still cancels
locally with no call. Only the ``requests`` session is faked: the real
``BoxNowClient`` builds every request.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import requests
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.services import OrderService
from shipping.models import ShippingProvider
from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode
from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowRetryableError,
)
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.services import BoxNowService
from shipping_boxnow.tasks import create_boxnow_shipment_for_order
from tests.unit.shipping_boxnow.test_client_error_mapping import (
    _response,
    _token,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("boxnow_configured_tenant"),
]

_CREATE = ("POST", "/api/v1/delivery-requests")
_LOOKUP = ("GET", "/api/v1/parcels")
_P410 = {
    "code": "P410",
    "message": (
        "Order number conflict. You are trying to create a delivery "
        "request for order ID that has already been created."
    ),
}


def _cancel(parcel_id: str) -> tuple[str, str]:
    return ("POST", f"/api/v1/parcels/{parcel_id}:cancel")


#: The locker every shipment here is created for, and every listed
#: parcel's ``destination.locationId`` unless a test says otherwise.
_LOCKER = "apm-0606"
#: One ``GET /api/v1/parcels`` per payment mode: the parameter defaults to
#: ``prepaid`` (API Manual v7.2, §6.5.3; partner-api-1.68.yaml).
_LOOKUPS = [_LOOKUP] * len(BoxNowPaymentMode.values)


def _parcel(
    parcel_id: str,
    state: str,
    *,
    delivery_request: dict | None = None,
    **extra: Any,
) -> dict:
    """One row of ``GET /api/v1/parcels`` (partner-api-1.68.yaml)."""
    return {
        "id": parcel_id,
        "state": state,
        "cancelationRequested": False,
        "deliveryRequest": {
            "orderNumber": "ignored",
            "paymentMode": BoxNowPaymentMode.PREPAID,
            "amountToBeCollected": "0.00",
            "destination": {"locationId": _LOCKER},
            **(delivery_request or {}),
        },
        **extra,
    }


def _page(*parcels: dict, next_token: str | None = None) -> Any:
    pagination = {"first": "a", "last": "a"}
    if next_token is not None:
        pagination["next"] = next_token
    return _response(
        200,
        {
            "pagination": pagination,
            "count": len(parcels),
            "data": list(parcels),
        },
    )


class _Listing:
    """What BoxNow lists under an ``orderNumber``, asked once per mode.

    Each request is answered with the rows of the payment mode it asks
    for, ``prepaid`` when it names none (the parameter's documented
    default), until every mode was asked.
    """

    def __init__(self, parcels: tuple[dict, ...]) -> None:
        self.parcels = parcels
        self.unasked = set(BoxNowPaymentMode.values)

    def answer(self, params: dict | None) -> Any:
        mode = (params or {}).get("paymentMode", BoxNowPaymentMode.PREPAID)
        self.unasked.discard(mode)
        return _page(
            *(
                row
                for row in self.parcels
                if row["deliveryRequest"]["paymentMode"] == mode
            )
        )


def _lookup(*parcels: dict) -> _Listing:
    return _Listing(parcels)


class FakeBoxNowHttp:
    """The ``requests`` session under ``BoxNowClient``.

    Each ``(method, path)`` answers from its own queue, in order; an entry
    that is an exception is raised instead, and a callable is called and
    its result answered (something that happens while BoxNow answers). A
    ``_Listing`` stays first in the queue until each mode was asked.
    Every request is recorded.
    """

    def __init__(self, **_: Any) -> None:
        self.answers: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.post = MagicMock(return_value=_token())

    def answer(self, route: tuple[str, str], *answers: Any) -> None:
        self.answers.setdefault(route, []).extend(answers)

    def request(self, *, method: str, url: str, **kwargs: Any) -> Any:
        route = (method, urlsplit(url).path)
        self.calls.append((*route, kwargs.get("params")))
        queue = self.answers.get(route)
        if not queue:
            raise AssertionError(f"unexpected BoxNow request {route}")
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, MagicMock):
            answer = answer()
        if isinstance(answer, _Listing):
            page = answer.answer(kwargs.get("params"))
            if answer.unasked:
                queue.insert(0, answer)
            return page
        return answer

    def routes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.calls]


@pytest.fixture(autouse=True)
def _private_cache():
    """The token cache, private to this test (see ``test_client.py``)."""
    local = LocMemCache(f"boxnow-orphan-{uuid4()}", {})
    with patch("django.core.cache.cache", local):
        yield


@pytest.fixture
def http():
    fake = FakeBoxNowHttp()
    with patch(
        "shipping_boxnow.services.BoxNowClient",
        partial(BoxNowClient, session=fake),
    ):
        yield fake


def _paid_order(**kwargs: Any):
    """A paid order waiting for its first parcel, so no tracking number.

    ``OrderFactory`` gives half of its orders a random one, which the
    assertions that nothing was saved would then read as ours.
    """
    kwargs.setdefault("tracking_number", "")
    return OrderFactory(
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        **kwargs,
    )


def _pending_shipment(order=None, **metadata: Any):
    return BoxNowShipmentFactory(
        order=order or _paid_order(),
        parcel_state=BoxNowParcelState.PENDING_CREATION,
        locker_external_id=_LOCKER,
        payment_mode=BoxNowPaymentMode.PREPAID,
        metadata=metadata,
    )


def _after_a_lost_answer(order=None):
    """A shipment whose creation reached BoxNow and lost its answer.

    What ``create_shipment_for_order`` leaves after a read timeout: the
    claim released, the retryable error recorded, no parcel number.
    """
    return _pending_shipment(
        order,
        mint_started_at=None,
        last_error={
            "type": "BoxNowAPIError",
            "code": None,
            "message": "BoxNow timeout after 10s",
            "status_code": 0,
            "response_text": "",
        },
    )


class TestACreationWhoseAnswerWasLost:
    def test_the_retry_takes_over_the_parcel_boxnow_already_holds(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(
            _CREATE,
            requests.ReadTimeout("read timed out"),
            _response(400, _P410),
        )
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        shipment.refresh_from_db()
        assert shipment.parcel_id is None

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, _CREATE, *_LOOKUPS]
        assert [params for *_, params in http.calls[2:]] == [
            {"orderNumber": str(order.id), "paymentMode": mode}
            for mode in BoxNowPaymentMode.values
        ]
        shipment.refresh_from_db()
        assert shipment.parcel_id == "9219709201"
        assert shipment.parcel_state == BoxNowParcelState.NEW
        assert shipment.metadata["mint_started_at"] is None
        assert shipment.metadata["last_error"] is None
        assert shipment.metadata["adopted_parcel"]["parcel_id"] == (
            "9219709201"
        )
        order.refresh_from_db()
        assert order.tracking_number == "9219709201"
        assert order.shipping_carrier == "boxnow"

    def test_the_task_reports_success_and_alerts_nobody(self, http):
        order = _paid_order()
        _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            # ``run``: the body, under the tenant this test bound
            # (``TenantTask.__call__`` would look up a Tenant row).
            result = create_boxnow_shipment_for_order.run(order.id)

        assert result["status"] == "ok"
        assert result["parcel_id"] == "9219709201"
        alert.assert_not_called()

    def test_a_parcel_taken_over_is_not_created_again(self, http):
        order = _paid_order()
        _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        BoxNowService.create_shipment_for_order(order)

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, *_LOOKUPS]

    @pytest.mark.parametrize(
        "parcels",
        [
            pytest.param([], id="boxnow-lists-none"),
            pytest.param(
                [_parcel("9219709201", "canceled")], id="only-a-canceled-one"
            ),
            pytest.param(
                [_parcel("9219709201", "in-transit")], id="one-past-new"
            ),
            pytest.param(
                [_parcel("9219709201", "new"), _parcel("9219709202", "new")],
                id="two-live-ones",
            ),
            pytest.param(
                [_parcel("9219709201", "new", cancelationRequested=True)],
                id="one-whose-cancel-was-asked",
            ),
        ],
    )
    def test_a_conflict_with_no_one_parcel_to_take_over_is_refused(
        self, http, parcels
    ):
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _lookup(*parcels))

        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.create_shipment_for_order(order)

        assert raised.value.code == "P410"
        assert not isinstance(raised.value, BoxNowRetryableError)
        shipment.refresh_from_db()
        assert shipment.parcel_id is None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.metadata["mint_started_at"] is None
        assert shipment.metadata["last_error"]["code"] == "P410"

    @pytest.mark.parametrize(
        ("delivery_request", "named"),
        [
            pytest.param(
                {"destination": {"locationId": "apm-0001"}},
                "apm-0001",
                id="another-locker",
            ),
            pytest.param(
                {
                    "paymentMode": BoxNowPaymentMode.COD,
                    "amountToBeCollected": "25.50",
                },
                "cod",
                id="cash-on-delivery",
            ),
            pytest.param(
                {"destination": {}}, "locationId None", id="no-locker-listed"
            ),
        ],
    )
    def test_a_parcel_created_for_other_terms_is_not_taken_over(
        self, http, delivery_request, named
    ):
        """Staff changed the locker (or the payment) and dispatched again.

        BoxNow's parcel still goes where the first request sent it, so
        taking it over would show the new locker on a parcel bound for
        the old one.
        """
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(
            _LOOKUP,
            _lookup(
                _parcel("9219709201", "new", delivery_request=delivery_request)
            ),
        )

        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.create_shipment_for_order(order)

        assert raised.value.code == "P410"
        assert "9219709201" in raised.value.message
        assert named in raised.value.message
        shipment.refresh_from_db()
        assert shipment.parcel_id is None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.metadata["last_error"]["code"] == "P410"
        order.refresh_from_db()
        assert not order.tracking_number

    def test_a_cod_parcel_with_the_same_terms_is_taken_over(self, http):
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        shipment.payment_mode = BoxNowPaymentMode.COD
        shipment.amount_to_be_collected = Money("25.50", "EUR")
        shipment.save()
        http.answer(_CREATE, _response(400, _P410))
        http.answer(
            _LOOKUP,
            _lookup(
                _parcel(
                    "9219709201",
                    "new",
                    delivery_request={
                        "paymentMode": BoxNowPaymentMode.COD,
                        "amountToBeCollected": "25.5",
                    },
                )
            ),
        )

        BoxNowService.create_shipment_for_order(order)

        shipment.refresh_from_db()
        assert shipment.parcel_id == "9219709201"

    def test_a_lookup_that_fails_leaves_the_retry_to_celery(self, http):
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, requests.ReadTimeout("read timed out"))

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)

        shipment.refresh_from_db()
        assert shipment.parcel_id is None
        assert shipment.metadata["mint_started_at"] is None

    def test_an_unreadable_lookup_is_refused_not_read_as_empty(self, http):
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _response(200, text="<html>maintenance</html>"))

        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.create_shipment_for_order(order)

        assert not isinstance(raised.value, BoxNowRetryableError)
        shipment.refresh_from_db()
        assert shipment.parcel_id is None


class TestCancellingAShipmentWithNoParcelNumber:
    def test_a_parcel_boxnow_holds_is_canceled_there(self, http):
        shipment = _after_a_lost_answer()
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == [*_LOOKUPS, _cancel("9219709201")]
        assert [params for *_, params in http.calls[:2]] == [
            {"orderNumber": str(shipment.order_id), "paymentMode": mode}
            for mode in BoxNowPaymentMode.values
        ]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"
        assert shipment.cancel_requested_at is not None
        [entry] = shipment.metadata["cancellations"]
        assert entry["reason"] == "customer"

    def test_a_canceled_parcel_beside_the_live_one_is_left_alone(self, http):
        shipment = _after_a_lost_answer()
        http.answer(
            _LOOKUP,
            _lookup(
                _parcel("9219709200", "canceled"),
                _parcel("9219709201", "new"),
            ),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == [*_LOOKUPS, _cancel("9219709201")]

    def test_a_cash_on_delivery_parcel_is_found_and_canceled(self, http):
        """``paymentMode`` defaults to ``prepaid`` in the lookup.

        API Manual v7.2, §6.5.3: "paymentMode ... Available values:
        prepaid, cod. Default value: prepaid"; partner-api-1.68.yaml,
        ``PaymentMode``: ``default: prepaid``. A lookup that names no mode
        never sees a COD parcel.
        """
        shipment = _after_a_lost_answer()
        http.answer(
            _LOOKUP,
            _lookup(
                _parcel(
                    "9219709201",
                    "new",
                    delivery_request={"paymentMode": BoxNowPaymentMode.COD},
                )
            ),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == [*_LOOKUPS, _cancel("9219709201")]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"

    def test_a_parcel_on_a_later_page_is_found_and_canceled(self, http):
        """``pagination.next``: "Token to use to get to the next page"."""
        shipment = _after_a_lost_answer()
        http.answer(
            _LOOKUP,
            _page(_parcel("9219709200", "canceled"), next_token="page-2"),
            _page(_parcel("9219709201", "new")),
            _page(),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == [*_LOOKUPS, _LOOKUP, _cancel("9219709201")]
        assert http.calls[1][2]["pageToken"] == "page-2"
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"

    def test_a_parcel_whose_cancel_was_only_requested_is_canceled(self, http):
        """``cancelationRequested`` is not ``canceled``.

        partner-api-1.68.yaml: "True if you or system administrator had
        requested parcel cancelation". The state still reads ``new``, so
        the parcel is cancelled by id ("Canceling a canceled parcel has no
        effect", §3.7) rather than taken for gone.
        """
        shipment = _after_a_lost_answer()
        http.answer(
            _LOOKUP,
            _lookup(_parcel("9219709201", "new", cancelationRequested=True)),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == [*_LOOKUPS, _cancel("9219709201")]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"

    @pytest.mark.parametrize(
        "parcels",
        [
            pytest.param([], id="none"),
            pytest.param(
                [_parcel("9219709201", "canceled")], id="only-a-canceled-one"
            ),
        ],
    )
    def test_nothing_live_at_boxnow_cancels_locally(self, http, parcels):
        shipment = _after_a_lost_answer()
        http.answer(_LOOKUP, _lookup(*parcels))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.routes() == _LOOKUPS
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id is None

    @pytest.mark.parametrize(
        ("answer", "retryable"),
        [
            pytest.param(
                requests.ReadTimeout("read timed out"), True, id="timeout"
            ),
            pytest.param(_response(503, text="busy"), True, id="5xx"),
            pytest.param(
                _response(400, {"code": "P400", "message": "bad"}),
                False,
                id="4xx",
            ),
            pytest.param(
                _response(200, text="<html>maintenance</html>"),
                False,
                id="unreadable",
            ),
        ],
    )
    def test_a_lookup_that_fails_refuses_the_cancel(
        self, http, answer, retryable
    ):
        shipment = _after_a_lost_answer()
        http.answer(_LOOKUP, answer)

        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.cancel_shipment(shipment, reason="customer")

        assert isinstance(raised.value, BoxNowRetryableError) is retryable
        assert http.routes() == [_LOOKUP]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.cancel_requested_at is None

    @pytest.mark.parametrize(
        "parcels",
        [
            pytest.param(
                [_parcel("9219709201", "in-transit")], id="one-past-new"
            ),
            pytest.param(
                [_parcel("9219709201", "new"), _parcel("9219709202", "new")],
                id="two-live-ones",
            ),
            pytest.param(
                [
                    _parcel(
                        "9219709201", "in-transit", cancelationRequested=True
                    )
                ],
                id="one-past-new-whose-cancel-was-requested",
            ),
        ],
    )
    def test_a_parcel_boxnow_will_not_cancel_is_refused(self, http, parcels):
        shipment = _after_a_lost_answer()
        http.answer(_LOOKUP, _lookup(*parcels))

        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.cancel_shipment(shipment, reason="customer")

        assert not isinstance(raised.value, BoxNowRetryableError)
        assert http.routes() == _LOOKUPS
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_a_creation_in_flight_refuses_without_asking(self, http):
        shipment = _pending_shipment(mint_started_at=timezone.now().isoformat())

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.cancel_shipment(shipment, reason="customer")

        assert http.calls == []
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_a_shipment_never_sent_cancels_with_no_call(self, http):
        order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        shipment = _pending_shipment(order)

        BoxNowService.cancel_shipment(shipment, reason="unpaid")

        assert http.calls == []
        http.post.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED


def _canceled_while_boxnow_answers(shipment, answer: Any):
    """BoxNow's answer, delivered after the row was cancelled locally.

    What a cancel made once the claim's TTL ran out looks like to phase 3
    of a slow creation.
    """

    def deliver() -> Any:
        type(shipment).objects.filter(pk=shipment.pk).update(
            parcel_state=BoxNowParcelState.CANCELED,
            cancel_requested_at=timezone.now(),
        )
        return answer

    return deliver


class TestAParcelCreatedForAShipmentCanceledMeanwhile:
    _CREATED = {"id": "42224", "parcels": [{"id": "9219709201"}]}

    def _assert_stays_canceled(self, shipment, order):
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id is None
        assert shipment.delivery_request_id is None
        assert shipment.metadata["mint_started_at"] is None
        order.refresh_from_db()
        assert not order.tracking_number

    def test_the_cancel_is_kept_and_the_parcel_canceled_at_boxnow(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(
            _CREATE,
            _canceled_while_boxnow_answers(
                shipment, _response(200, self._CREATED)
            ),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, _cancel("9219709201")]
        self._assert_stays_canceled(shipment, order)
        [minted] = shipment.metadata["minted_after_cancel"]
        assert minted["parcel_id"] == "9219709201"
        assert minted["delivery_request_id"] == "42224"
        assert minted["canceled_at_boxnow"]
        assert "orphan_delivery_requests" not in shipment.metadata

    def test_a_parcel_boxnow_will_not_cancel_is_recorded_for_ops(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(
            _CREATE,
            _canceled_while_boxnow_answers(
                shipment, _response(200, self._CREATED)
            ),
        )
        http.answer(_cancel("9219709201"), _response(503, text="busy"))

        BoxNowService.create_shipment_for_order(order)

        self._assert_stays_canceled(shipment, order)
        [minted] = shipment.metadata["minted_after_cancel"]
        assert "503" in minted["cancel_error"]
        [orphan] = shipment.metadata["orphan_delivery_requests"]
        assert orphan["parcel_id"] == "9219709201"
        assert orphan["delivery_request_id"] == "42224"
        assert "503" in orphan["cancel_error"]

    def test_a_parcel_taken_over_after_p410_is_canceled_too(self, http):
        order = _paid_order()
        shipment = _after_a_lost_answer(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(
            _LOOKUP,
            _canceled_while_boxnow_answers(
                shipment, _lookup(_parcel("9219709201", "new"))
            ),
        )
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, *_LOOKUPS, _cancel("9219709201")]
        self._assert_stays_canceled(shipment, order)

    def test_no_reissue_while_that_parcel_is_being_canceled(self, http):
        """A reissue then would take over the parcel being cancelled.

        Its creation answers P410 and the lookup still lists the parcel
        as ``new``, so the new shipment would keep a parcel the pending
        cancel is about to cancel at BoxNow.
        """
        order = _paid_order()
        shipment = _pending_shipment(order)
        reissue: list[BaseException | None] = []

        def reissue_while_boxnow_cancels() -> Any:
            try:
                BoxNowService.reset_shipment_for_reissue(shipment)
            except BoxNowAPIError as exc:
                reissue.append(exc)
            else:
                reissue.append(None)
            return _response(200, {})

        http.answer(
            _CREATE,
            _canceled_while_boxnow_answers(
                shipment, _response(200, self._CREATED)
            ),
        )
        http.answer(_cancel("9219709201"), reissue_while_boxnow_cancels)

        BoxNowService.create_shipment_for_order(order)

        [refused] = reissue
        assert isinstance(refused, BoxNowRetryableError)
        assert "9219709201" in refused.message
        self._assert_stays_canceled(shipment, order)

    @pytest.mark.parametrize(
        ("outcome", "minted_ago"),
        [
            pytest.param(
                {"canceled_at_boxnow": "2026-11-14T00:00:00+00:00"},
                timedelta(0),
                id="canceled-at-boxnow",
            ),
            pytest.param(
                {"cancel_error": "BoxNow API 503"},
                timedelta(0),
                id="cancel-refused",
            ),
            pytest.param(
                {},
                timedelta(seconds=BoxNowService._MINT_CLAIM_TTL_SECONDS + 1),
                id="no-cancel-running-any-more",
            ),
        ],
    )
    def test_a_reissue_once_that_cancel_is_over_goes_through(
        self, outcome, minted_ago
    ):
        shipment = _pending_shipment(
            minted_after_cancel=[
                {
                    "delivery_request_id": "42224",
                    "parcel_id": "9219709201",
                    "minted_at": (timezone.now() - minted_ago).isoformat(),
                    **outcome,
                }
            ]
        )
        type(shipment).objects.filter(pk=shipment.pk).update(
            parcel_state=BoxNowParcelState.CANCELED
        )

        BoxNowService.reset_shipment_for_reissue(shipment)

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION


class TestStaffAreToldWhenBoxNowCannotBeAsked:
    def test_a_staff_cancel_goes_through_and_leaves_a_note(self, http):
        order = _paid_order(
            shipping_provider=ShippingProvider.objects.get(code="boxnow"),
            shipping_kind="pickup_point",
        )
        shipment = _after_a_lost_answer(order)
        http.answer(_LOOKUP, _response(503, text="busy"))

        canceled, _ = OrderService.cancel_order(
            order,
            reason="staff",
            refund_payment=False,
            customer_initiated=False,
        )

        assert canceled.status == OrderStatus.CANCELED
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        shipment_cancel = canceled.metadata["cancellation"]["shipment_cancel"]
        assert shipment_cancel["dispatched"] is False
        assert "error" in shipment_cancel
        notes = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
        ]
        assert any("may still be delivered" in note for note in notes)
