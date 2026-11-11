from django.db import models
from django.utils.translation import gettext_lazy as _


class StockState(models.TextChoices):
    """Where a product's stock stands against its own threshold.

    Out of stock is stock 0. Low is stock above 0 and at or below the
    product's ``low_stock_threshold``; a threshold of 0 turns the alerts
    off, so that product is never low. Anything else is in stock.
    ``Product.stock_state`` decides it for a row and
    ``ProductQuerySet.by_stock_state`` for a queryset.
    """

    IN_STOCK = "in_stock", _("In stock")
    LOW_STOCK = "low_stock", _("Low stock")
    OUT_OF_STOCK = "out_of_stock", _("Out of stock")


#: The states that call for restocking: the low-stock email and the staff
#: agent's ``lowStock`` list name these.
RESTOCK_STATES = (StockState.LOW_STOCK, StockState.OUT_OF_STOCK)
