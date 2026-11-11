from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldBooleanSwitchWidget,
)


class ApplyDiscountForm(forms.Form):
    discount_percent = forms.DecimalField(
        label=_("Discount Percentage"),
        min_value=Decimal(0),
        max_value=Decimal(100),
        decimal_places=2,
        max_digits=5,
        widget=UnfoldAdminDecimalFieldWidget(attrs={"step": "0.01"}),
        error_messages={
            "required": _("Please enter a discount percentage."),
            "invalid": _("Please enter a valid number."),
            "min_value": _("Discount cannot be negative."),
            "max_value": _("Discount cannot exceed 100%."),
        },
        help_text=_(
            # xgettext:no-python-format — "% d" here is a literal percent.
            "Enter a value between 0 and 100. For example, 25 for 25% discount."
        ),
    )

    apply_to_inactive = forms.BooleanField(
        label=_("Apply to inactive products"),
        required=False,
        widget=UnfoldBooleanSwitchWidget,
        help_text=_("Check this to also apply discount to inactive products"),
    )
