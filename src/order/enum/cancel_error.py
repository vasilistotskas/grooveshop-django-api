from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderCancelErrorType(models.TextChoices):
    """``error.type`` of a refused customer cancel (``POST /order/{id}/cancel``).

    The stable code a client branches on. The ``detail`` beside it is
    translated into the request's language, so matching its text works
    in one language at most.
    """

    PARCEL_WITH_COURIER = (
        "parcel_with_courier",
        _("Parcel already with the courier"),
    )
    COURIER_UNREACHABLE = (
        "courier_unreachable",
        _("Courier could not be reached"),
    )
