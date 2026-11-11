from django.db import models
from django.utils.translation import gettext_lazy as _


class ExpenseCategory(models.TextChoices):
    """What an ``Expense`` was spent on.

    ``SHIPPING`` is the one the profit report reads on its own: carrier
    invoices are what delivering the orders cost, so they come off the
    contribution margin. Every other category is an operating expense.
    Payment-provider fees are not a category: the provider reports them
    per payment (``PaymentTransaction.fee``).
    """

    SHIPPING = "SHIPPING", _("Shipping (carrier invoices)")
    MARKETING = "MARKETING", _("Marketing")
    PACKAGING = "PACKAGING", _("Packaging")
    SOFTWARE = "SOFTWARE", _("Software and subscriptions")
    RENT = "RENT", _("Rent and utilities")
    PAYROLL = "PAYROLL", _("Payroll")
    OTHER = "OTHER", _("Other")


#: The categories that are the cost of delivering orders, not of running the
#: store: they sit above the contribution margin in the profit report.
DELIVERY_EXPENSE_CATEGORIES = (ExpenseCategory.SHIPPING,)
