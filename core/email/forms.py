from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib.admin.options import HORIZONTAL
from django.utils.translation import gettext_lazy as _
from unfold.widgets import (
    UnfoldAdminRadioSelectWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from order.models import Order

RECENT_ORDERS = 10


class EmailPreviewForm(forms.Form):
    """What the preview panel renders: a template, in a language, from
    sample data or one of the store's recent orders."""

    template = forms.ChoiceField(widget=forms.HiddenInput)
    # Filters the template list in the browser; rendered outside the
    # <form>, so it is never submitted.
    search = forms.CharField(
        required=False,
        widget=UnfoldAdminTextInputWidget(
            attrs={
                "type": "search",
                "x-model": "query",
                "placeholder": _("Search templates..."),
            }
        ),
    )
    language = forms.ChoiceField(
        label=_("Language"),
        choices=settings.LANGUAGES,
        initial=settings.PARLER_DEFAULT_LANGUAGE_CODE,
        widget=UnfoldAdminSelectWidget,
    )
    order = forms.TypedChoiceField(
        label=_("Data"),
        coerce=int,
        empty_value=None,
        required=False,
        widget=UnfoldAdminSelectWidget,
        help_text=_("Order emails can render one of the recent orders."),
    )
    format = forms.ChoiceField(
        label=_("Format"),
        choices=[("html", "HTML"), ("text", _("Text"))],
        initial="html",
        widget=UnfoldAdminRadioSelectWidget(radio_style=HORIZONTAL),
    )

    def __init__(self, *args, template_names, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["template"].choices = [
            (name, name) for name in template_names
        ]
        # values_list, not .only(): Order.__init__ reads deferred fields,
        # and loading one builds another instance, which reads again.
        recent = Order.objects.order_by("-created_at").values_list(
            "pk", "first_name", "last_name"
        )[:RECENT_ORDERS]
        self.fields["order"].choices = [
            ("", _("Sample data")),
            *((pk, f"#{pk} · {first} {last}") for pk, first, last in recent),
        ]
