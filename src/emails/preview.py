"""What the admin's email preview shows: any registered email, rendered.

One function, :func:`build_preview`, renders a type through
``emails.render.render``, the function the sending pipeline calls: from a
real order when the type is about an order and one is chosen, else from the
type's own ``sample``. Nothing here builds a subject or a context of its
own, so the preview is the mail that is sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django import forms
from django.conf import settings
from django.contrib.admin.options import HORIZONTAL
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from unfold.widgets import (
    UnfoldAdminRadioSelectWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from emails.recipients import Recipient
from emails.registry import registry
from emails.render import render
from emails.types import EmailType, Owner, RenderEnv
from order.emails import ORDER_EMAIL_KEYS, NothingToPreview, preview_object
from order.models import Order

logger = logging.getLogger(__name__)

RECENT_ORDERS = 10

#: The address of the made-up recipient of a sample.
SAMPLE_RECIPIENT = "customer@example.com"


@dataclass(frozen=True, slots=True)
class Preview:
    subject: str
    html: str
    text: str
    #: Why there is nothing to show, else ``None``.
    error: str | None = None


class EmailPreviewForm(forms.Form):
    """What the preview panel renders: an email, in a language, from sample
    data or one of the store's recent orders."""

    type_key = forms.ChoiceField(widget=forms.HiddenInput)
    # Filters the email list in the browser; rendered outside the <form>,
    # so it is never submitted.
    search = forms.CharField(
        required=False,
        widget=UnfoldAdminTextInputWidget(
            attrs={
                "type": "search",
                "x-model": "query",
                "placeholder": _("Search emails..."),
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

    def __init__(
        self,
        *args,
        owner: Owner = Owner.STORE,
        with_orders: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["type_key"].choices = [
            (email_type.key, email_type.key)
            for email_type in registry.types_for(owner)
        ]
        if not with_orders:
            # A viewer without the order permission previews sample data
            # only: a real order carries a customer's name and address.
            del self.fields["order"]
            return
        # values_list, not .only(): Order.__init__ reads deferred fields,
        # and loading one builds another instance, which reads again.
        recent = Order.objects.order_by("-created_at").values_list(
            "pk", "first_name", "last_name"
        )[:RECENT_ORDERS]
        self.fields["order"].choices = [
            ("", _("Sample data")),
            *((pk, f"#{pk} · {first} {last}") for pk, first, last in recent),
        ]


def build_preview(
    email_type: EmailType, *, order_id: int | None, language: str
) -> Preview:
    """*email_type* rendered in *language*.

    With an *order_id* and a type that is about an order, the email that
    order would be sent; a type that is not about an order, or no order,
    renders the type's sample. An order that is missing, or has no record
    the email is about (no refund, no receipt ...), is reported, never
    replaced by sample data that would pass for the order's email.
    """
    # The whole of it runs in the chosen language, the error's text too: it
    # is a lazy message, read when the preview is built.
    with translation.override(language):
        try:
            obj, env = preview_subject(
                email_type, order_id=order_id, language=language
            )
            rendered = render(email_type, obj, env)
        except NothingToPreview as nothing:
            return Preview(subject="", html="", text="", error=str(nothing))
        except Exception as error:
            logger.exception(
                "Email preview of %s failed",
                email_type.key,
                extra={"email_type": email_type.key, "order_id": order_id},
            )
            return Preview(subject="", html="", text="", error=str(error))
    return Preview(
        subject=rendered.subject, html=rendered.html, text=rendered.text
    )


def preview_subject(
    email_type: EmailType, *, order_id: int | None, language: str
) -> tuple[object, RenderEnv]:
    """What a preview (and the admin's Send test) renders: the real record
    an order email is about when one is chosen, else the type's sample, with
    the environment of its recipient. Raises ``NothingToPreview`` when the
    chosen order has no such record."""
    real = None
    if order_id and email_type.key in ORDER_EMAIL_KEYS:
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            raise NothingToPreview(f"Order {order_id} not found.")
        real = preview_object(email_type.key, order)
    env = _env(email_type, real, language)
    return (real if real is not None else email_type.sample(env)), env


def _env(email_type: EmailType, real: object, language: str) -> RenderEnv:
    """Who the previewed mail is for: its real recipient when there is a
    record, the address of a made-up one otherwise."""
    found = email_type.recipients(real) if real is not None else []
    address = found[0].address if found else SAMPLE_RECIPIENT
    return RenderEnv(
        language=language,
        recipient=Recipient(address=address, language=language),
        is_preview=True,
    )
