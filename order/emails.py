"""The order emails, built in ONE place.

Every email about an order is two things before it is a message: a
subject and a template context. They used to be assembled inline in the
Celery task that sends the mail, and again, by hand and slightly
differently, in the admin's email preview, so the preview showed an email
that was never sent (a reduced context, an English subject made up for it).

Each builder here takes the objects its email is about and returns an
:class:`OrderEmail`: the template, the translated subject, the context and
the language, with no side effect. The sending task calls it and sends;
the preview calls the same function with a real order and renders the same
thing (``core.email.preview_service``). A change to what an email says is
made once.

Each builder runs under ``_email_language(order, language)``: the
recipient's language is active, so the subject and every lazy label in the
context resolve in it, and the order's translated rows (pay way, country,
region) are switched to it whatever language they were loaded in;
:meth:`OrderEmail.render` renders under the same language.

What stays with the tasks: choosing WHEN to send (idempotency claims,
status gates), the recipient, ``Reply-To`` and list headers, attachments,
and the bookkeeping after a send.

Not here yet: the emails that are not about an order (cart reminders, gift
cards, product alerts, B2B, billing, account). They belong to the planned
email-type catalogue, which will give every type one entry holding its
builder; ``ORDER_EMAIL_KINDS`` below is that entry for the order emails.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING, Any

from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import get_template, render_to_string
from django.utils import translation
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django_stubs_ext import StrOrPromise
from djmoney.money import Money

from core.utils.email_context import build_email_context
from core.utils.tenant_urls import get_tenant_frontend_url
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.payment import payment_provider_label
from order.payment_ledger import PaymentLedger
from pay_way.enum.settlement import PaySettlement
from recommendation.order_email import EmailStrip, order_email_strip

if TYPE_CHECKING:
    from order.models import Order, PaymentTransaction

logger = logging.getLogger(__name__)

TEMPLATE_DIR = "emails/order"


@dataclass(frozen=True)
class OrderEmail:
    """One order email, ready to render and send."""

    #: Template path without extension: ``<template>.txt`` and
    #: ``<template>.html`` exist side by side.
    template: str
    subject: str
    context: dict[str, Any]
    language: str
    #: The suggestions the confirmation shows, kept so the sender can
    #: record their impression once the mail has gone.
    suggestion_strip: EmailStrip | None = None

    def render(self) -> tuple[str, str]:
        """``(text, html)`` bodies, rendered in the email's language."""
        with translation.override(self.language):
            return (
                render_to_string(f"{self.template}.txt", self.context),
                render_to_string(f"{self.template}.html", self.context),
            )


class NothingToPreview(Exception):
    """The order has no record this email is about (no refund, no
    receipt, no shipment ...), so there is nothing real to render."""


# ---------------------------------------------------------------------------
# Subjects. Module-level so the preview can show the real subject of an
# email it renders from sample data, where there is an order NUMBER but no
# order to build from.
# ---------------------------------------------------------------------------


def order_received_subject(order_id: int) -> str:
    return _("Order Received - #{order_id}").format(order_id=order_id)


def order_payment_confirmed_subject(order_id: int) -> str:
    return _("Payment Confirmed - Order #{order_id}").format(order_id=order_id)


def order_status_update_subject(order_id: int, status: str) -> str:
    return _("Order #{order_id} Status Update - {status}").format(
        order_id=order_id, status=OrderStatus(status).label
    )


def order_shipped_subject(order_id: int) -> str:
    return _("Your Order #{order_id} Has Shipped").format(order_id=order_id)


def payment_failed_subject(order_id: int) -> str:
    return _("Payment Failed - Order #{order_id}").format(order_id=order_id)


def refund_confirmation_subject(order_id: int) -> str:
    return _("Refund Processed - Order #{order_id}").format(order_id=order_id)


def admin_new_order_subject(order_id: int) -> str:
    return _("New order — #{order_id}").format(order_id=order_id)


def document_issued_subject(
    order_id: int, *, document_number: str, is_credit: bool
) -> str:
    template = (
        _("Credit receipt {document_number} for your order #{order_id}")
        if is_credit
        else _("Receipt {document_number} for your order #{order_id}")
    )
    return template.format(document_number=document_number, order_id=order_id)


def dispute_subject(
    order_id: int, *, provider_name: StrOrPromise, lost: bool, site_name: str
) -> str:
    template = (
        _("[%(site)s] %(provider)s dispute lost — Order #%(order)s")
        if lost
        else _("[%(site)s] %(provider)s dispute opened — Order #%(order)s")
    )
    return template % {
        "site": site_name,
        "provider": provider_name,
        "order": order_id,
    }


def acs_out_for_delivery_subject() -> str:
    return _("Your ACS parcel is out for delivery")


def boxnow_parcel_at_locker_subject() -> str:
    return _("Your BOX NOW parcel arrived at the locker")


# ---------------------------------------------------------------------------
# Customer emails
# ---------------------------------------------------------------------------


def is_paid_online(order: Order) -> bool:
    """Whether the order's online payment has been captured: the line
    between the "payment confirmed" and the "order received" email."""
    pay_way = order.pay_way
    return bool(
        pay_way
        and PaySettlement(pay_way.settlement) == PaySettlement.ONLINE
        and order.payment_status == PaymentStatus.COMPLETED
    )


# The translated rows an order email prints through the order:
# ``{{ order.country }}``, the region, the pay way's name and its
# payment instructions.
_TRANSLATED_RELATIONS = ("pay_way", "country", "region")


@contextmanager
def _email_language(order: Order, language: str) -> Generator[None]:
    """Build one email in *language*, the order's translated rows included.

    parler fixes a model instance's language when it is loaded, so a pay
    way or country the sending task loaded before it chose the email's
    language printed its name and instructions in the worker's language
    (an English email showed the Greek payment instructions). Every
    builder enters through here, so how a caller loaded the order no
    longer decides the language of what the email says.
    """
    with translation.override(language):
        for name in _TRANSLATED_RELATIONS:
            related = getattr(order, name, None)
            if related is not None:
                related.set_current_language(language)
        yield


def _lines(order: Order):
    """The order's lines, read NOW, in the active language.

    The builders call this under ``translation.override``. parler fixes a
    model instance's language when it is created, so lines (and their
    products) a task loaded before the override named every product in the
    worker's language, whatever language the customer's email is in. A
    query made here is created in the email's language; the translations
    are prefetched, so it costs two queries, not one per line.
    """
    return order.items.select_related("product").prefetch_related(
        "product__translations"
    )


def _confirmation_context(order: Order, language: str) -> tuple[dict, Any]:
    """The context shared by the two confirmation emails, and the
    suggestion strip it carries (None when there is none)."""
    pay_way = order.pay_way

    payment_instructions = ""
    payment_instructions_text = ""
    # Instructions only where the shopper has to act to pay: a
    # transfer made off the platform, or BOX NOW's pay-on-the-go,
    # paid through the link the carrier sends before the locker
    # opens. Cash handed to the courier needs nothing from the
    # shopper (owner review 2026-10-16, webside order 413). The
    # "include the order number in the payment reference" line
    # belongs to the transfer alone: nobody writes a reference
    # for the others. All of it only while the shopper still
    # owes the payment: a transfer the store already marked
    # received (``order.is_paid``) gets neither, nor a promise of
    # a confirmation that has happened.
    settled = order.is_paid
    settlement = PaySettlement(pay_way.settlement) if pay_way else None
    payment_reference_needed = (
        not settled and settlement == PaySettlement.OFFLINE_TRANSFER
    )
    if (
        pay_way
        and not settled
        and settlement
        in (PaySettlement.OFFLINE_TRANSFER, PaySettlement.CARRIER_TERMINAL)
    ):
        payment_instructions = (
            pay_way.safe_translation_getter("instructions", any_language=True)
            or ""
        )
        # ``PayWay.instructions`` is authored in the admin WYSIWYG
        # editor, so it carries HTML (e.g. ``<div>…</div>``). The
        # HTML email renders it with ``|safe``; the plain-text email
        # needs the tags stripped and entities unescaped, otherwise
        # the customer sees raw ``<div>`` markup.
        payment_instructions_text = unescape(
            strip_tags(payment_instructions)
        ).strip()

    # Does a SECOND email actually follow this one? Only when
    # the shopper still owes us a payment we will confirm
    # ourselves — an online payment that has not completed, or a
    # bank transfer we mark as received. Money the carrier
    # collects at the door or at the locker is never confirmed
    # by us (COD sits PENDING until the courier pays out, ~4
    # days later), so promising a confirmation email there
    # promises something that never arrives.
    expects_payment_confirmation = bool(
        pay_way and not settled and not pay_way.is_collected_on_delivery
    )

    # Suggestions are an extra in this email, never a reason to
    # lose it: a failure building them is logged and the
    # confirmation goes out without the strip.
    try:
        suggestion_strip = order_email_strip(order, language=language)
    except Exception:
        logger.exception(
            "Order #%s confirmation: suggestions failed, sending without them",
            order.id,
            extra={"order_id": order.id},
        )
        suggestion_strip = None

    context = build_email_context(
        language=language,
        order=order,
        items=_lines(order),
        suggested_products=suggestion_strip.items if suggestion_strip else [],
        pay_way=pay_way,
        payment_instructions=payment_instructions,
        payment_instructions_text=payment_instructions_text,
        is_paid=is_paid_online(order),
        expects_payment_confirmation=expects_payment_confirmation,
        collected_on_delivery=bool(
            pay_way and pay_way.is_collected_on_delivery
        ),
        payment_reference_needed=payment_reference_needed,
    )
    return context, suggestion_strip


def _confirmation(
    order: Order, *, language: str, template: str, subject: Callable[[int], str]
) -> OrderEmail:
    with _email_language(order, language):
        context, strip = _confirmation_context(order, language)
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/{template}",
            subject=subject(order.id),
            context=context,
            language=language,
            suggestion_strip=strip,
        )


def order_received_email(order: Order, *, language: str) -> OrderEmail:
    return _confirmation(
        order,
        language=language,
        template="order_received",
        subject=order_received_subject,
    )


def order_payment_confirmed_email(order: Order, *, language: str) -> OrderEmail:
    return _confirmation(
        order,
        language=language,
        template="order_payment_confirmed",
        subject=order_payment_confirmed_subject,
    )


def order_confirmation_email(order: Order, *, language: str) -> OrderEmail:
    """The email an order gets when it is placed: the payment
    confirmation once an online payment has been captured, the "received"
    email otherwise."""
    build = (
        order_payment_confirmed_email
        if is_paid_online(order)
        else order_received_email
    )
    return build(order, language=language)


def _template_exists(base: str) -> bool:
    try:
        get_template(f"{base}.txt")
        get_template(f"{base}.html")
    except TemplateDoesNotExist:
        return False
    return True


def order_status_update_email(
    order: Order, status: str, *, language: str
) -> OrderEmail:
    """The status-change email: the status's own template when there is
    one, the generic one otherwise. Only a MISSING template falls back; a
    template that exists and fails to render is a bug that must surface."""
    template = f"{TEMPLATE_DIR}/order_{status.lower()}"
    if not _template_exists(template):
        logger.warning(
            "Template %s not found, using generic template",
            template,
            extra={"order_id": order.id, "status": status},
        )
        template = f"{TEMPLATE_DIR}/order_status_generic"
    with _email_language(order, language):
        return OrderEmail(
            template=template,
            subject=order_status_update_subject(order.id, status),
            context=build_email_context(
                language=language,
                order=order,
                items=_lines(order),
                status=status,
                status_display=OrderStatus(status).label,
            ),
            language=language,
        )


def order_shipped_email(order: Order, *, language: str) -> OrderEmail:
    with _email_language(order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/order_shipped",
            subject=order_shipped_subject(order.id),
            context=build_email_context(
                language=language,
                order=order,
                tracking_number=order.tracking_number,
                carrier=order.shipping_carrier,
            ),
            language=language,
        )


def payment_failed_email(order: Order, *, language: str) -> OrderEmail:
    with _email_language(order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/payment_failed",
            subject=payment_failed_subject(order.id),
            context=build_email_context(
                language=language,
                order=order,
                retry_url=get_tenant_frontend_url(
                    f"/account/orders/{order.id}", language=language
                ),
            ),
            language=language,
        )


def refund_confirmation_email(
    refund: PaymentTransaction, *, language: str
) -> OrderEmail:
    """The email about ONE refund (a ``PaymentTransaction`` REFUND)."""
    order = refund.order
    with _email_language(order, language):
        # What THIS refund paid back and what is still held, so a partial
        # refund does not read as the whole order refunded.
        currency = refund.amount.currency
        still_held = (
            PaymentLedger.captured(order)
            - PaymentLedger.refunded(order)
            - PaymentLedger.charged_back(order)
        )
        context = build_email_context(
            language=language,
            order=order,
            status="REFUNDED",
            # A lazy label, resolved by the render under the email's own
            # language: ``get_FOO_display()`` would resolve it at once,
            # under whatever locale the worker last left active, and a
            # customer got the status word in the wrong language.
            status_display=(
                PaymentStatus(order.payment_status).label
                if order.payment_status
                else PaymentStatus.REFUNDED.label
            ),
            items=_lines(order),
            refund_amount=refund.amount,
            payment_remaining=(
                Money(still_held, currency) if still_held > 0 else None
            ),
        )
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/order_refunded",
            subject=refund_confirmation_subject(order.id),
            context=context,
            language=language,
        )


def document_issued_email(
    order: Order, document: Any, *, is_credit: bool, language: str
) -> OrderEmail:
    """The email that carries a registered receipt (or credit) PDF."""
    with _email_language(order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/retail_document_issued",
            subject=document_issued_subject(
                order.id,
                document_number=document.document_number,
                is_credit=is_credit,
            ),
            context=build_email_context(
                language=language,
                order=order,
                document_number=document.document_number,
                issue_date=document.issue_date,
                mydata_mark=document.mydata_mark,
                is_credit=is_credit,
            ),
            language=language,
        )


def acs_out_for_delivery_email(shipment: Any, *, language: str) -> OrderEmail:
    with _email_language(shipment.order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/acs_out_for_delivery",
            subject=acs_out_for_delivery_subject(),
            context=build_email_context(
                language=language,
                order=shipment.order,
                shipment=shipment,
                voucher_no=shipment.voucher_no,
            ),
            language=language,
        )


def boxnow_parcel_at_locker_email(
    shipment: Any, *, language: str
) -> OrderEmail:
    locker = shipment.locker
    with _email_language(shipment.order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/boxnow_parcel_at_locker",
            subject=boxnow_parcel_at_locker_subject(),
            context=build_email_context(
                language=language,
                order=shipment.order,
                shipment=shipment,
                locker=locker,
                locker_address=(
                    locker.address_line_1
                    if locker is not None
                    else shipment.locker_external_id
                ),
                parcel_id=shipment.parcel_id,
            ),
            language=language,
        )


# ---------------------------------------------------------------------------
# Store-facing emails
# ---------------------------------------------------------------------------


def admin_new_order_email(
    order: Order, *, language: str, staff_email: str
) -> OrderEmail:
    """The store's "new order" alert; ``INFO_EMAIL`` is the staff address
    the template shows, not the public contact one."""
    with _email_language(order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/admin_new_order",
            subject=admin_new_order_subject(order.id),
            context=build_email_context(
                language=language,
                order=order,
                items=_lines(order),
                pay_way=order.pay_way,
                INFO_EMAIL=staff_email,
            ),
            language=language,
        )


def dispute_notification_email(
    order: Order,
    *,
    dispute_id: str,
    provider_code: str,
    lost: bool,
    language: str,
) -> OrderEmail:
    """The store's alert that a provider dispute opened, or (``lost``)
    closed against the store and was recorded as a chargeback."""
    from tenant.credentials import tenant_site_name

    reason = (order.metadata or {}).get("dispute_reason", "unknown")
    chargeback = (
        order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGEBACK,
            provider_reference=dispute_id,
        ).first()
        if lost
        else None
    )
    provider_name = payment_provider_label(provider_code) or provider_code
    with _email_language(order, language):
        return OrderEmail(
            template=f"{TEMPLATE_DIR}/dispute_notification",
            subject=dispute_subject(
                order.id,
                provider_name=provider_name,
                lost=lost,
                site_name=tenant_site_name(),
            ),
            context=build_email_context(
                language=language,
                order=order,
                dispute_id=dispute_id,
                reason=reason,
                chargeback=chargeback,
                lost=lost,
                provider_name=provider_name,
            ),
            language=language,
        )


# ---------------------------------------------------------------------------
# The preview's view of the same emails
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderEmailKind:
    """How the admin preview reaches one order email from an order."""

    #: The subject for an order NUMBER alone, for previews on sample data.
    sample_subject: Callable[[int], str]
    #: The email a real order would produce, built by the same function
    #: the sending task calls. Raises :class:`NothingToPreview` when the
    #: order has nothing this email is about.
    from_order: Callable[[Order, str], OrderEmail]


def _staff_email() -> str:
    from tenant.credentials import tenant_contact_email

    return tenant_contact_email()


def _status_kind(status: OrderStatus) -> OrderEmailKind:
    return OrderEmailKind(
        sample_subject=lambda order_id: order_status_update_subject(
            order_id, status
        ),
        from_order=lambda order, language: order_status_update_email(
            order, status, language=language
        ),
    )


def _refund_from_order(order: Order, language: str) -> OrderEmail:
    refund = (
        order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)
        .order_by("-pk")
        .first()
    )
    if refund is None:
        raise NothingToPreview(_("This order has no refund to describe."))
    return refund_confirmation_email(refund, language=language)


def _document_from_order(order: Order, language: str) -> OrderEmail:
    credit = order.credit_documents.order_by("-pk").first()
    invoice = getattr(order, "invoice", None)
    document, is_credit = (
        (credit, True) if credit is not None else (invoice, False)
    )
    if document is None:
        raise NothingToPreview(_("This order has no receipt to send."))
    return document_issued_email(
        order, document, is_credit=is_credit, language=language
    )


def _dispute_from_order(order: Order, language: str) -> OrderEmail:
    chargeback = (
        order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGEBACK
        )
        .order_by("-pk")
        .first()
    )
    if chargeback is None:
        raise NothingToPreview(_("This order has no dispute to report."))
    return dispute_notification_email(
        order,
        dispute_id=chargeback.provider_reference,
        provider_code=chargeback.provider,
        lost=True,
        language=language,
    )


def _shipment_from_order(related_name: str, build) -> Callable:
    def from_order(order: Order, language: str) -> OrderEmail:
        shipment = getattr(order, related_name, None)
        if shipment is None:
            raise NothingToPreview(_("This order has no such shipment."))
        return build(shipment, language=language)

    return from_order


def _sample_document_subject(order_id: int) -> str:
    return document_issued_subject(
        order_id, document_number="A-00001", is_credit=False
    )


def _sample_dispute_subject(order_id: int) -> str:
    from tenant.credentials import tenant_site_name

    return dispute_subject(
        order_id,
        provider_name=payment_provider_label("stripe") or "stripe",
        lost=False,
        site_name=tenant_site_name(),
    )


#: Keyed by template name (``core/templates/emails/order/<name>``).
ORDER_EMAIL_KINDS: dict[str, OrderEmailKind] = {
    "order_received": OrderEmailKind(
        sample_subject=order_received_subject,
        from_order=lambda order, language: order_received_email(
            order, language=language
        ),
    ),
    "order_payment_confirmed": OrderEmailKind(
        sample_subject=order_payment_confirmed_subject,
        from_order=lambda order, language: order_payment_confirmed_email(
            order, language=language
        ),
    ),
    "order_shipped": OrderEmailKind(
        sample_subject=order_shipped_subject,
        from_order=lambda order, language: order_shipped_email(
            order, language=language
        ),
    ),
    "order_delivered": _status_kind(OrderStatus.DELIVERED),
    "order_completed": _status_kind(OrderStatus.COMPLETED),
    "order_canceled": _status_kind(OrderStatus.CANCELED),
    "order_returned": _status_kind(OrderStatus.RETURNED),
    "order_status_generic": OrderEmailKind(
        sample_subject=lambda order_id: order_status_update_subject(
            order_id, OrderStatus.PROCESSING
        ),
        from_order=lambda order, language: order_status_update_email(
            order, order.status, language=language
        ),
    ),
    "order_refunded": OrderEmailKind(
        sample_subject=refund_confirmation_subject,
        from_order=_refund_from_order,
    ),
    "payment_failed": OrderEmailKind(
        sample_subject=payment_failed_subject,
        from_order=lambda order, language: payment_failed_email(
            order, language=language
        ),
    ),
    "admin_new_order": OrderEmailKind(
        sample_subject=admin_new_order_subject,
        from_order=lambda order, language: admin_new_order_email(
            order,
            language=language,
            staff_email=_staff_email(),
        ),
    ),
    "retail_document_issued": OrderEmailKind(
        sample_subject=_sample_document_subject,
        from_order=_document_from_order,
    ),
    "dispute_notification": OrderEmailKind(
        sample_subject=_sample_dispute_subject,
        from_order=_dispute_from_order,
    ),
    "acs_out_for_delivery": OrderEmailKind(
        sample_subject=lambda order_id: acs_out_for_delivery_subject(),
        from_order=_shipment_from_order(
            "acs_shipment", acs_out_for_delivery_email
        ),
    ),
    "boxnow_parcel_at_locker": OrderEmailKind(
        sample_subject=lambda order_id: boxnow_parcel_at_locker_subject(),
        from_order=_shipment_from_order(
            "boxnow_shipment", boxnow_parcel_at_locker_email
        ),
    ),
}
