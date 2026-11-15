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
from shipping_boxnow.models import BoxNowShipment
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

    def test_a_shipment_reset_for_reissue_meanwhile_is_left_as_it_is(
        self, http
    ):
        """A reset that lands while BoxNow is asked keeps its pending row.

        Another cancel recorded the shipment and staff reset it for a new
        parcel while this cancel was asking BoxNow. Writing the parcel it
        found and CANCELED would undo the reset, and the queued creation
        would then return the dead parcel instead of a new one.
        """
        shipment = _after_a_lost_answer()

        def reset_meanwhile():
            BoxNowShipment.objects.filter(pk=shipment.pk).update(
                parcel_state=BoxNowParcelState.CANCELED,
                cancel_requested_at=timezone.now(),
            )
            BoxNowService.reset_shipment_for_reissue(
                BoxNowShipment.objects.get(pk=shipment.pk)
            )
            return _response(200, {})

        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), reset_meanwhile)

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.parcel_id is None

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


class TestACancelAfterACreationOfUncertainOutcome:
    """#690: a creation that timed out may have made a parcel BoxNow does
    not list yet. Until ``BOXNOW_CREATION_SETTLE_SECONDS`` have passed, an
    empty lookup by ``orderNumber`` is not proof that none exists."""

    def test_an_empty_lookup_refuses_the_cancel_while_it_is_fresh(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        http.answer(_LOOKUP, _lookup())

        with pytest.raises(BoxNowRetryableError) as raised:
            BoxNowService.cancel_shipment(shipment, reason="customer")

        assert "uncertain outcome" in raised.value.message
        assert http.routes() == [_CREATE, *_LOOKUPS]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.cancel_requested_at is None
        assert "cancellations" not in shipment.metadata

    def test_after_the_settle_time_the_empty_lookup_is_authoritative(
        self, http, settings
    ):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, _response(503, text="busy"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        stamp = timezone.now() - timedelta(
            seconds=settings.BOXNOW_CREATION_SETTLE_SECONDS + 1
        )
        BoxNowShipment.objects.filter(pk=shipment.pk).update(
            metadata={
                **BoxNowShipment.objects.get(pk=shipment.pk).metadata,
                "creation_uncertain_at": stamp.isoformat(),
            }
        )
        http.answer(_LOOKUP, _lookup())

        BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id is None

    def test_the_settle_time_is_a_setting(self, http, settings):
        settings.BOXNOW_CREATION_SETTLE_SECONDS = 0
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        http.answer(_LOOKUP, _lookup())

        BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED

    def test_a_parcel_listed_meanwhile_is_canceled_at_boxnow(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"
        assert shipment.metadata["creation_uncertain_at"] is None

    @pytest.mark.parametrize(
        "refusal",
        [
            pytest.param(
                _response(400, {"code": "P401", "message": "Invalid locker"}),
                id="validation",
            ),
            pytest.param(
                _response(403, {"message": "Forbidden"}), id="auth-layer"
            ),
            pytest.param(
                _response(401, {"error": "invalid_client"}), id="token-401"
            ),
        ],
    )
    def test_a_later_refusal_keeps_it(self, http, refusal):
        """A refusal of the retry proves only that the retry created
        nothing: the timed-out request before it may still have, and
        nothing documents that BoxNow checks P410 before an auth or a
        validation answer. Only a parcel saved or found ends it, or the
        settle time."""
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        if refusal.status_code == 401:
            # The token endpoint refusing the store's credentials.
            from django.core.cache import cache

            cache.clear()
            http.post.return_value = refusal
        else:
            http.answer(_CREATE, refusal)
        with pytest.raises(BoxNowAPIError) as raised:
            BoxNowService.create_shipment_for_order(order)
        assert not isinstance(raised.value, BoxNowRetryableError)
        http.post.return_value = _token()
        http.answer(_LOOKUP, _lookup())

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is not None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    @pytest.mark.parametrize(
        "lookup",
        [
            pytest.param(_lookup(), id="nothing-listed"),
            # A canceled parcel this shipment never recorded: something
            # it does not know made a request under the orderNumber, so
            # the conflict proves nothing about what else BoxNow holds.
            # Contrast the reissue case below.
            pytest.param(
                _lookup(_parcel("9219709201", "canceled")),
                id="only-an-unrecorded-canceled-one",
            ),
            pytest.param(_response(503, text="busy"), id="lookup-fails"),
        ],
    )
    def test_a_p410_with_no_stamp_stamps_it(self, http, lookup):
        """A worker that died between the POST and phase 3 left no stamp.
        After the claim's TTL the retry gets P410, which proves BoxNow
        holds a request under the orderNumber; a take-over that cannot
        find its parcel leaves the creation uncertain."""
        order = _paid_order()
        stale = timezone.now() - timedelta(
            seconds=BoxNowService._MINT_CLAIM_TTL_SECONDS + 1
        )
        shipment = _pending_shipment(order, mint_started_at=stale.isoformat())
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, lookup)
        with pytest.raises(BoxNowAPIError):
            BoxNowService.create_shipment_for_order(order)
        http.answer(_LOOKUP, _lookup())

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is not None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_a_reissue_whose_p410_is_its_own_old_parcel_is_not_stamped(
        self, http
    ):
        """After "Create BoxNow parcel now" resets a canceled shipment, a
        new creation gets P410 because the old canceled request still holds
        the orderNumber (the documented reissue case). When that recorded
        parcel is all BoxNow lists, the conflict has a known cause: no
        stamp, so a cancel of the never-created shipment is not blocked
        for the settle time."""
        order = _paid_order()
        shipment = _pending_shipment(order)
        BoxNowShipment.objects.filter(pk=shipment.pk).update(
            parcel_id="9219709201", parcel_state=BoxNowParcelState.CANCELED
        )
        BoxNowService.reset_shipment_for_reissue(shipment)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "canceled")))
        with pytest.raises(BoxNowAPIError):
            BoxNowService.create_shipment_for_order(order)
        assert not BoxNowShipment.objects.get(pk=shipment.pk).metadata.get(
            "creation_uncertain_at"
        )
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "canceled")))

        BoxNowService.cancel_shipment(shipment, reason="staff")

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED

    def test_a_parcel_already_saved_clears_it(self, http):
        """Phase 3 finding its own parcel already on the row (saved by
        another path meanwhile) knows the outcome too."""
        order = _paid_order()
        shipment = _pending_shipment(
            order, creation_uncertain_at=timezone.now().isoformat()
        )
        BoxNowShipment.objects.filter(pk=shipment.pk).update(
            parcel_id="9219709201", parcel_state=BoxNowParcelState.NEW
        )

        BoxNowService._persist_parcel(
            order,
            shipment.pk,
            delivery_request_id=None,
            parcel_id="9219709201",
            record={},
        )

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is None

    def test_a_p410_keeps_it(self, http):
        """P410 proves an earlier request created something; a lookup that
        lists nothing yet must not end the uncertainty."""
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(
            _CREATE,
            requests.ReadTimeout("read timed out"),
            _response(400, _P410),
        )
        http.answer(_LOOKUP, _lookup(), _lookup())
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        with pytest.raises(BoxNowAPIError):
            BoxNowService.create_shipment_for_order(order)

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.cancel_shipment(shipment, reason="customer")

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is not None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_a_saved_parcel_clears_it(self, http):
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
        assert BoxNowShipment.objects.get(pk=shipment.pk).metadata[
            "creation_uncertain_at"
        ]

        BoxNowService.create_shipment_for_order(order)

        shipment.refresh_from_db()
        assert shipment.parcel_id == "9219709201"
        assert shipment.metadata["creation_uncertain_at"] is None


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


class TestACanceledOrderGetsNoLiveParcel:
    """A staff or system order cancel goes on when the courier step is
    refused (a creation of uncertain outcome still settling, one in
    flight), so the order is canceled while its shipment still waits for
    a parcel. The creation must never then leave a live parcel on it."""

    _CREATED = {"id": "42224", "parcels": [{"id": "9219709201"}]}

    def test_a_retry_after_the_order_was_canceled_cancels_instead(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        type(order).objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED
        )
        order.refresh_from_db()
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, *_LOOKUPS, _cancel("9219709201")]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        order.refresh_from_db()
        assert not order.tracking_number

    def test_a_retry_while_it_settles_creates_nothing(self, http):
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, requests.ReadTimeout("read timed out"))
        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)
        type(order).objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED
        )
        order.refresh_from_db()
        http.answer(_LOOKUP, _lookup())

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, *_LOOKUPS]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        assert shipment.parcel_id is None

    def test_a_parcel_created_while_the_order_was_canceled_is_canceled(
        self, http
    ):
        order = _paid_order()
        shipment = _pending_shipment(order)

        def cancel_the_order_while_boxnow_answers() -> Any:
            type(order).objects.filter(pk=order.pk).update(
                status=OrderStatus.CANCELED
            )
            return _response(200, self._CREATED)

        http.answer(_CREATE, cancel_the_order_while_boxnow_answers)
        http.answer(_cancel("9219709201"), _response(200, {}))

        BoxNowService.create_shipment_for_order(order)

        assert http.routes() == [_CREATE, _cancel("9219709201")]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id is None
        [minted] = shipment.metadata["minted_after_cancel"]
        assert minted["parcel_id"] == "9219709201"
        assert minted["canceled_at_boxnow"]
        order.refresh_from_db()
        assert not order.tracking_number


def _settled(order=None, **metadata: Any) -> BoxNowShipment:
    """A creation whose answer was lost, stamped uncertain longer ago than
    ``BOXNOW_CREATION_SETTLE_SECONDS``: what the creation task's retries
    leave once they run out (about 30 s of backoff, far inside the window).
    """
    from django.conf import settings as django_settings

    shipment = _after_a_lost_answer(order)
    stamp = timezone.now() - timedelta(
        seconds=django_settings.BOXNOW_CREATION_SETTLE_SECONDS + 1
    )
    BoxNowShipment.objects.filter(pk=shipment.pk).update(
        metadata={
            **shipment.metadata,
            "creation_uncertain_at": stamp.isoformat(),
            **metadata,
        }
    )
    shipment.refresh_from_db()
    return shipment


def _canceled_order():
    order = _paid_order()
    type(order).objects.filter(pk=order.pk).update(status=OrderStatus.CANCELED)
    order.refresh_from_db()
    return order


def _notes(order) -> list[str]:
    return [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]


class TestTheCreationTaskOnACanceledOrder:
    """The task names a canceled order's outcome as a cancel, never as a
    created parcel nor as a creation to retry from the admin."""

    def test_a_parcel_canceled_instead_is_not_reported_as_created(self, http):
        order = _canceled_order()
        _after_a_lost_answer(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), _response(200, {}))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            result = create_boxnow_shipment_for_order.run(order.id)

        assert result["status"] == "order_canceled"
        alert.assert_not_called()

    def test_a_cancel_boxnow_refuses_is_not_a_creation_alert(self, http):
        order = _canceled_order()
        _after_a_lost_answer(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "in-transit")))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            result = create_boxnow_shipment_for_order.run(order.id)

        assert result["status"] == "boxnow_cancel_refused"
        alert.assert_not_called()
        assert any("did not cancel" in note for note in _notes(order))


class TestTheSweepSettlesUncertainCreations:
    """#712: once the settle time has passed, the sweep asks BoxNow once
    more and settles the shipment, since the creation task's retries ran
    out long before."""

    def test_a_canceled_orders_parcel_is_canceled_at_boxnow(self, http):
        shipment = _settled(_canceled_order())
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))
        http.answer(_cancel("9219709201"), _response(200, {}))

        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "canceled"
        assert http.routes() == [*_LOOKUPS, _cancel("9219709201")]
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id == "9219709201"

    def test_a_canceled_order_with_nothing_listed_cancels_locally(self, http):
        shipment = _settled(_canceled_order())
        http.answer(_LOOKUP, _lookup())

        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "canceled"
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert shipment.parcel_id is None

    def test_a_cancel_boxnow_refuses_is_noted_once(self, http):
        order = _canceled_order()
        shipment = _settled(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "in-transit")))

        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)
        again = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "cancel_refused"
        assert again == "not_due"
        assert http.routes() == _LOOKUPS
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is None
        assert any("did not cancel" in note for note in _notes(order))

    def test_a_live_orders_parcel_is_saved(self, http):
        order = _paid_order()
        shipment = _settled(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "new")))

        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "taken_over"
        shipment.refresh_from_db()
        assert shipment.parcel_id == "9219709201"
        assert shipment.parcel_state == BoxNowParcelState.NEW
        assert shipment.metadata["creation_uncertain_at"] is None
        assert shipment.metadata["mint_started_at"] is None
        order.refresh_from_db()
        assert order.tracking_number == "9219709201"

    def test_a_live_order_with_nothing_listed_is_settled_and_staff_told(
        self, http
    ):
        shipment = _settled(_paid_order())
        http.answer(_LOOKUP, _lookup())

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_created"
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is None
        assert shipment.metadata["mint_started_at"] is None
        assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION
        alert.assert_called_once()
        assert "Create BoxNow parcel now" in alert.call_args.kwargs["remedy"]

    def test_staff_already_told_of_a_refusal_are_not_told_again(self, http):
        shipment = _settled(
            _paid_order(),
            last_error={
                "type": "BoxNowAPIError",
                "code": "P401",
                "message": "Invalid locker",
                "status_code": 400,
                "response_text": "",
                "staff_alerted": True,
            },
        )
        http.answer(_LOOKUP, _lookup())

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_created"
        alert.assert_not_called()

    def test_a_parcel_for_other_terms_is_left_to_staff(self, http):
        shipment = _settled(_paid_order())
        http.answer(
            _LOOKUP,
            _lookup(
                _parcel(
                    "9219709201",
                    "new",
                    delivery_request={"destination": {"locationId": "other"}},
                )
            ),
        )

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_taken_over"
        shipment.refresh_from_db()
        assert shipment.parcel_id is None
        assert shipment.metadata["creation_uncertain_at"] is None
        alert.assert_called_once()

    def test_a_lookup_that_fails_leaves_it_for_the_next_run(self, http):
        shipment = _settled(_paid_order())
        stamp = shipment.metadata["creation_uncertain_at"]
        http.answer(_LOOKUP, _response(503, text="busy"))

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.settle_uncertain_creation(shipment.pk)

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] == stamp
        assert shipment.metadata["mint_started_at"] is None

    @pytest.mark.parametrize(
        "fresh_key",
        [
            pytest.param("creation_uncertain_at", id="still-settling"),
            pytest.param("mint_started_at", id="creation-in-flight"),
        ],
    )
    def test_nothing_is_asked_before_it_is_due(self, http, fresh_key):
        # Stamped when the test runs, never at collection: a run that
        # starts minutes after collection would find them stale.
        shipment = _settled(
            _paid_order(), **{fresh_key: timezone.now().isoformat()}
        )

        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_due"
        assert http.calls == []

    def test_the_task_settles_every_due_shipment(self, http):
        from shipping_boxnow.tasks import settle_uncertain_boxnow_creations

        due = _settled(_canceled_order())
        _pending_shipment(
            _paid_order(), creation_uncertain_at=timezone.now().isoformat()
        )
        http.answer(_LOOKUP, _lookup())

        result = settle_uncertain_boxnow_creations.run()

        assert result == {"canceled": 1, "not_due": 1}
        due.refresh_from_db()
        assert due.parcel_state == BoxNowParcelState.CANCELED


class TestTheThirdReview:
    """What the sweep and the creation task decide at the edges."""

    def test_staff_alerted_of_a_non_4xx_refusal_are_not_told_again(self, http):
        """The creation task alerts on every BoxNowAPIError it does
        not retry, an unreadable lookup (status 200) included, so the
        sweep reads what was alerted, never a status range."""
        order = _paid_order()
        shipment = _pending_shipment(order)
        http.answer(_CREATE, _response(400, _P410))
        http.answer(_LOOKUP, _response(200, text="<html>maintenance</html>"))
        with patch("shipping.alerts.alert_admins_shipment_creation_failed"):
            result = create_boxnow_shipment_for_order.run(order.id)
        assert result["status"] == "boxnow_api_error"
        _age_the_stamp(shipment)
        http.answer(_LOOKUP, _lookup())

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_created"
        alert.assert_not_called()

    def test_a_refused_cancel_in_the_task_ends_the_uncertainty(self, http):
        """Reported once, by the task; the sweep has nothing left."""
        order = _canceled_order()
        shipment = _settled(order)
        http.answer(_LOOKUP, _lookup(_parcel("9219709201", "in-transit")))

        result = create_boxnow_shipment_for_order.run(order.id)
        outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert result["status"] == "boxnow_cancel_refused"
        assert outcome == "not_due"
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is None
        assert sum("did not cancel" in note for note in _notes(order)) == 1

    def test_an_order_canceled_during_the_lookup_is_canceled(self, http):
        """The sweep re-reads the order before acting on what it
        found; a canceled one gets its cancel, not a "Create" alert."""
        order = _paid_order()
        shipment = _settled(order)

        def cancel_the_order_while_boxnow_answers():
            type(order).objects.filter(pk=order.pk).update(
                status=OrderStatus.CANCELED
            )
            return _lookup()

        http.answer(_LOOKUP, cancel_the_order_while_boxnow_answers)
        http.answer(_LOOKUP, _lookup())

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "canceled"
        alert.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED

    def test_the_fanout_includes_suspended_stores(self):
        """A canceled order's parcel may be live at BoxNow whether
        or not its store is suspended."""
        from tenant import tasks as tenant_tasks

        with patch("tenant.tasks.run_for_all_tenants") as run:
            tenant_tasks.fanout_settle_uncertain_boxnow_creations()

        run.assert_called_once_with(
            "shipping_boxnow.tasks.settle_uncertain_boxnow_creations",
            include_suspended=True,
        )

    def test_a_lookup_boxnow_refuses_is_handed_to_staff(self, http):
        """A deterministic refusal of the lookup would answer the same on
        every run: staff are told at once and the uncertainty ends."""
        shipment = _settled(_paid_order())
        http.answer(_LOOKUP, _response(400, {"code": "P400", "message": "bad"}))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "lookup_refused"
        alert.assert_called_once()
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] is None
        assert shipment.metadata["mint_started_at"] is None

    def test_the_task_counts_a_shipment_boxnow_did_not_answer_for(self, http):
        """No answer is left for the next run, and the rest go on."""
        from shipping_boxnow.tasks import settle_uncertain_boxnow_creations

        shipment = _settled(_paid_order())
        http.answer(_LOOKUP, _response(503, text="busy"))

        result = settle_uncertain_boxnow_creations.run()

        assert result == {"unanswered": 1}
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"]


def _age_the_stamp(shipment) -> None:
    """Move a stamp the creation wrote past the settle time."""
    from django.conf import settings as django_settings

    metadata = BoxNowShipment.objects.get(pk=shipment.pk).metadata
    assert metadata["creation_uncertain_at"]
    stamp = timezone.now() - timedelta(
        seconds=django_settings.BOXNOW_CREATION_SETTLE_SECONDS + 1
    )
    BoxNowShipment.objects.filter(pk=shipment.pk).update(
        metadata={**metadata, "creation_uncertain_at": stamp.isoformat()}
    )


def _token_answers(http, response) -> None:
    """The token endpoint's next answers; the token cache starts empty."""
    from django.core.cache import cache

    cache.clear()
    http.post.return_value = response


class TestNoAnswerKeepsTheUncertainty:
    """A BoxNow outage (a token 5xx, a 429) is no answer and revoked
    credentials say nothing about a parcel: the uncertainty stays, so the
    sweep settles it once BoxNow answers again."""

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param(_response(503, text="busy"), id="token-5xx"),
            pytest.param(_response(429, text="slow down"), id="token-429"),
        ],
    )
    def test_a_token_outage_is_left_for_the_next_run(self, http, token):
        shipment = _settled(_paid_order())
        stamp = shipment.metadata["creation_uncertain_at"]
        _token_answers(http, token)

        with (
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
            pytest.raises(BoxNowRetryableError),
        ):
            BoxNowService.settle_uncertain_creation(shipment.pk)

        alert.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] == stamp
        assert shipment.metadata["mint_started_at"] is None

    def test_a_rate_limited_lookup_is_left_for_the_next_run(self, http):
        shipment = _settled(_paid_order())
        stamp = shipment.metadata["creation_uncertain_at"]
        http.answer(_LOOKUP, _response(429, {"message": "Too many"}))

        with pytest.raises(BoxNowRetryableError):
            BoxNowService.settle_uncertain_creation(shipment.pk)

        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"] == stamp

    def test_revoked_credentials_keep_every_stamp_and_alert_once(self, http):
        from shipping_boxnow.tasks import settle_uncertain_boxnow_creations

        first = _settled(_paid_order())
        second = _settled(_canceled_order())
        _token_answers(http, _response(401, {"error": "invalid_client"}))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            settle_uncertain_boxnow_creations.run()
            settle_uncertain_boxnow_creations.run()

        alert.assert_called_once()
        assert "credentials" in alert.call_args.kwargs["remedy"]
        for shipment in (first, second):
            shipment.refresh_from_db()
            assert shipment.metadata["creation_uncertain_at"]
            assert shipment.metadata["mint_started_at"] is None
            assert shipment.parcel_state == BoxNowParcelState.PENDING_CREATION

    def test_revoked_credentials_on_a_canceled_order_keep_the_stamp(self, http):
        order = _canceled_order()
        shipment = _settled(order)
        _token_answers(http, _response(403, {"error": "forbidden"}))

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            result = create_boxnow_shipment_for_order.run(order.id)

        assert result["status"] == "boxnow_cancel_refused"
        alert.assert_not_called()
        shipment.refresh_from_db()
        assert shipment.metadata["creation_uncertain_at"]


class TestANewAttemptIsAlertedAgain:
    def test_a_new_claim_drops_the_old_alert_record(self, http):
        """Staff were alerted about a refusal; a later attempt whose
        answer is cut off must not inherit that record, or neither the task
        nor the sweep tells anyone about it."""
        order = _paid_order()
        shipment = _pending_shipment(
            order,
            mint_started_at=None,
            last_error={
                "type": "BoxNowAPIError",
                "code": "P401",
                "message": "Invalid locker",
                "status_code": 400,
                "response_text": "",
                "staff_alerted": True,
            },
        )
        http.answer(_CREATE, requests.exceptions.ChunkedEncodingError("cut"))
        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            BoxNowService.create_shipment_for_order(order)
        assert (
            "staff_alerted"
            not in (
                BoxNowShipment.objects.get(pk=shipment.pk).metadata[
                    "last_error"
                ]
            )
        )
        _age_the_stamp(shipment)
        http.answer(_LOOKUP, _lookup())

        with patch(
            "shipping.alerts.alert_admins_shipment_creation_failed"
        ) as alert:
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_created"
        alert.assert_called_once()


def _locked_tables(queries) -> list[str]:
    """The table each ``SELECT ... FOR UPDATE`` names first, in order."""
    import re

    tables = []
    for query in queries:
        sql = query["sql"]
        if "FOR UPDATE" in sql:
            match = re.search(r'FROM "(\w+)"', sql)
            tables.append(match.group(1) if match else sql[:40])
    return tables


class TestTheSweepLocksTheOrderFirst:
    """An order cancel locks the order, then the shipment
    (``OrderService._cancel_order_locked`` → ``cancel_shipment``). The
    sweep takes them in that order too, and decides on a live order's
    empty lookup and clears its stamp in one locked step, so a cancel
    cannot slip in between."""

    def test_order_then_shipment_and_one_step_to_settle(self, http):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        shipment = _settled(_paid_order())
        http.answer(_LOOKUP, _lookup())

        with (
            patch("shipping.alerts.alert_admins_shipment_creation_failed"),
            CaptureQueriesContext(connection) as captured,
        ):
            outcome = BoxNowService.settle_uncertain_creation(shipment.pk)

        assert outcome == "not_created"
        assert _locked_tables(captured.captured_queries) == [
            "order_order",
            "shipping_boxnow_boxnowshipment",
            "order_order",
            "shipping_boxnow_boxnowshipment",
        ]
