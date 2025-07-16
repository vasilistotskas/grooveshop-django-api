from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _


class ApplyDiscountForm(forms.Form):
    discount_percent = forms.DecimalField(
        label=_("Discount Percentage"),
        required=True,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        decimal_places=2,
        max_digits=5,
        widget=forms.NumberInput(
            attrs={
                "type": "number",
                "placeholder": "0.00",
                "step": "0.01",
                "min": "0",
                "max": "100",
                "class": "border border-gray-300 bg-white font-medium min-w-20 placeholder-gray-400 rounded-lg shadow-sm text-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent dark:bg-gray-900 dark:border-gray-600 dark:text-gray-100 dark:placeholder-gray-500 px-3 py-2 w-full",
            }
        ),
        error_messages={
            "required": _("Please enter a discount percentage."),
            "invalid": _("Please enter a valid number."),
            "min_value": _("Discount cannot be negative."),
            "max_value": _("Discount cannot exceed 100%."),
        },
        help_text=_(
            "Enter a value between 0 and 100. For example, 25 for 25% discount."
        ),
    )

    apply_to_inactive = forms.BooleanField(
        label=_("Apply to inactive products"),
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={
                "class": "rounded border-gray-300 text-primary-600 focus:ring-primary-500 dark:border-gray-600 dark:bg-gray-700"
            }
        ),
        help_text=_("Check this to also apply discount to inactive products"),
    )

    def clean_discount_percent(self):
        value = self.cleaned_data.get("discount_percent")
        if value is not None:
            return Decimal(str(value))
        return value
