from django.db import models
from django.utils.translation import gettext_lazy as _


class StockLogReason(models.TextChoices):
    """Stable code for a stock change whose reason is always the same text.

    Stored instead of the sentence so the row is not frozen in the
    language of whoever caused it; ``StockLog.reason_display`` translates
    it when shown. A change with its own wording keeps that in the free
    text ``StockLog.reason``.
    """

    MANUAL_ADJUSTMENT = (
        "manual_adjustment",
        _("Manual stock adjustment via admin panel"),
    )
