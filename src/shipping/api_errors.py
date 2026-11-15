"""API errors the carrier views answer with.

The domain errors live in ``shipping.exceptions``; these are what a view
raises for one of them, so the API error contract
(``docs/error-contract.md``) gives every carrier the same body.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


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
