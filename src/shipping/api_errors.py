"""API errors the carrier views answer with.

The domain errors live in ``shipping.exceptions``; these are what a view
raises for one of them, so the API error contract
(``docs/error-contract.md``) gives every carrier the same body.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException

from shipping.exceptions import CarrierBusyError, CarrierUnavailableError


class CarrierUnavailable(APIException):
    """A 503 for a :class:`shipping.exceptions.CarrierUnavailableError`.

    The courier gave no answer to act on (unreachable, timed out, failed
    on its side, rejected the call at its auth layer, or has no
    credentials here), so nothing is known about what was asked and the
    same request may succeed later. Distinct from the courier's own
    refusal, which a view answers with the courier's code. The carrier's
    error text goes to the log, never into ``detail``: it is written for
    us, not for a client.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "carrier_unavailable"
    default_detail = _(
        "The courier cannot be reached right now. Try again in a moment."
    )


class CarrierBusy(APIException):
    """A 409 for a :class:`shipping.exceptions.CarrierBusyError`.

    The same work is already running here (a creation or a cancel in
    flight, a creation still settling), so nothing was asked of the
    courier. ``detail`` is the refusal's own text, which says what to
    wait for; it is ours, never the courier's.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "carrier_request_in_progress"
    default_detail = _(
        "This shipment is already being worked on. Try again in a few minutes."
    )


class PickupPointCheckUnavailable(APIException):
    """A 503 for a :class:`shipping.exceptions.PickupPointCheckUnavailableError`.

    The point is not judged, so the answer names no field: the shopper's
    choice may be right, and the same request may succeed after
    ``Retry-After`` (``PICKUP_POINT_CHECK_RETRY_AFTER_SECONDS``, sent by
    DRF's handler from ``wait``).
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "pickup_point_check_unavailable"
    default_detail = _(
        "The pickup point cannot be checked right now. Try again in a moment."
    )

    def __init__(self) -> None:
        super().__init__()
        self.wait = settings.PICKUP_POINT_CHECK_RETRY_AFTER_SECONDS


def unanswered(exc: CarrierUnavailableError) -> APIException:
    """The API error for a carrier error the courier did not judge: a 409
    with the refusal's text when the work is already running here, else
    the 503 outage answer."""
    if isinstance(exc, CarrierBusyError):
        text = getattr(exc, "message", "") or getattr(exc, "error_message", "")
        return CarrierBusy(detail=text or None)
    return CarrierUnavailable()
