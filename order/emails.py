"""The order emails: their types, their builders and their samples.

Each email about an order is an :class:`~emails.types.EmailType`, declared
here next to the code that reads the order for it (``emails.EmailsConfig``
imports this module at boot and then freezes the catalogue). A type's
``build_context`` is the ONE reader of the order for that email: the
sending task (``emails.send.send_email``) and the admin preview
(``core.email.preview_service``) both go through ``emails.render.render``,
so what the preview shows is what the order is sent. A change to what an
email says is made once.

Every builder runs under ``_email_language(order, language)``: the
recipient's language is active, so every lazy label in the context
resolves in it, and the order's translated rows (pay way, country, region)
are switched to it whatever language they were loaded in. The subject is a
msgid with plain named fields, filled from the context by the pipeline
(``emails.render.format_subject``).

What stays with the tasks (``order.tasks``, ``shipping_acs.tasks``,
``shipping_boxnow.tasks``): choosing WHEN to send (idempotency claims,
status gates) and the bookkeeping after a send.

Each type has a ``sample``: an unsaved object graph the preview renders
when no order is chosen. A sample carries its own lines and totals, so
nothing reads the database for it (``_is_sample``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from html import unescape
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone, translation
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from djmoney.money import Money

from core.store_settings import store_setting
from core.utils.tenant_urls import get_tenant_frontend_url
from emails.declare import customer_email, staff_email
from emails.gates import CarrierGate, FeatureGate, StoreSwitchGate
from emails.recipients import Recipient, customer_of
from emails.types import EmailType, RenderEnv
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.payment import payment_provider_label
from order.payment_ledger import PaymentLedger
from pay_way.enum.settlement import PaySettlement
from recommendation.order_email import EmailStrip, order_email_strip
from tenant.credentials import tenant_site_name
from tenant.features import feature_on

if TYPE_CHECKING:
    from order.models import Order, PaymentTransaction

logger = logging.getLogger(__name__)

TEMPLATE_DIR = "emails/order"

#: The attribute a confirmation's builder leaves on the order: the strip
#: its mail shows, which the sending task records as an impression once the
#: mail has gone (``suggestion_strip_of``).
_STRIP_ATTRIBUTE = "email_suggestion_strip"

#: The key of the email each status change sends. PENDING, PROCESSING and
#: SHIPPED have none: the confirmation and the shipping notification own
#: those moments (``order.tasks.send_order_status_update_email``).
STATUS_EMAIL_KEYS: Mapping[str, str] = {
    OrderStatus.DELIVERED: "order.delivered",
    OrderStatus.COMPLETED: "order.completed",
    OrderStatus.CANCELED: "order.canceled",
    OrderStatus.RETURNED: "order.returned",
    OrderStatus.REFUNDED: "order.status_refunded",
}


#: The statuses whose email invites the shopper to review what arrived.
REVIEW_INVITING_STATUSES = frozenset(
    {OrderStatus.DELIVERED, OrderStatus.COMPLETED}
)

#: The fragment the storefront's product page reads to open the review form.
REVIEW_FORM_FRAGMENT = "#write-review"


class NothingToPreview(Exception):
    """The order has no record this email is about (no refund, no
    receipt, no shipment ...), so there is nothing real to render."""


@dataclass(frozen=True, slots=True)
class OrderDispute:
    """What a dispute email is about: the order, the provider's dispute
    and the provider that raised it."""

    order: Order
    dispute_id: str
    provider_code: str


# ---------------------------------------------------------------------------
# Building blocks
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


def _is_sample(order: Order) -> bool:
    """Whether *order* is a preview sample: never saved, so there is
    nothing to query for it."""
    return order._state.adding


def _lines(order: Order):
    """The order's lines, read NOW, in the active language.

    The builders call this under ``translation.override``. parler fixes a
    model instance's language when it is created, so lines (and their
    products) a task loaded before the override named every product in the
    worker's language, whatever language the customer's email is in. A
    query made here is created in the email's language; the translations
    are prefetched, so it costs two queries, not one per line. A sample
    carries its own lines.
    """
    if _is_sample(order):
        return vars(order)["sample_items"]
    return order.items.select_related("product").prefetch_related(
        "product__translations"
    )


def suggestion_strip_of(order: Order) -> EmailStrip | None:
    """The suggestions the confirmation mail built for *order* showed."""
    return getattr(order, _STRIP_ATTRIBUTE, None)


def _confirmation_context(order: Order, language: str) -> dict[str, Any]:
    """The context shared by the two confirmation emails."""
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
    setattr(order, _STRIP_ATTRIBUTE, suggestion_strip)

    return {
        "order_id": order.id,
        "order": order,
        "items": _lines(order),
        "suggested_products": (
            suggestion_strip.items if suggestion_strip else []
        ),
        "pay_way": pay_way,
        "payment_instructions": payment_instructions,
        "payment_instructions_text": payment_instructions_text,
        "is_paid": is_paid_online(order),
        "expects_payment_confirmation": expects_payment_confirmation,
        "collected_on_delivery": bool(
            pay_way and pay_way.is_collected_on_delivery
        ),
        "payment_reference_needed": payment_reference_needed,
    }


# ---------------------------------------------------------------------------
# Builders: ``build_context(obj, env)`` of each type
# ---------------------------------------------------------------------------


def _confirmation(order: Order, env: RenderEnv) -> dict[str, Any]:
    with _email_language(order, env.language):
        return _confirmation_context(order, env.language)


def _review_links(order: Order, language: str) -> list[dict[str, str]]:
    """One "write a review" link per distinct product of *order*, to the
    storefront page that opens the review form (``REVIEW_FORM_FRAGMENT``).

    Empty when the store has product reviews off, or when it requires a
    purchase to review and the order has no account: a guest order can
    never count as a verified purchase (``product.managers.review.
    purchase_items`` matches on the order's user), so the link would only
    lead to a refusal.
    """
    if not feature_on("product_reviews"):
        return []
    if not order.user_id and store_setting("PRODUCT_REVIEWS_PURCHASE_REQUIRED"):
        return []
    products = {line.product_id: line.product for line in _lines(order)}
    return [
        {
            "name": product.safe_translation_getter("name", any_language=True)
            or product.slug,
            "url": (
                get_tenant_frontend_url(
                    product.storefront_path, language=language
                )
                + REVIEW_FORM_FRAGMENT
            ),
        }
        for product in products.values()
    ]


def _status_update(order: Order, env: RenderEnv, status: str) -> dict[str, Any]:
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "items": _lines(order),
            "status": status,
            "status_display": OrderStatus(status).label,
            "review_links": (
                _review_links(order, env.language)
                if status in REVIEW_INVITING_STATUSES
                else []
            ),
        }


def _order_shipped(order: Order, env: RenderEnv) -> dict[str, Any]:
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "tracking_number": order.tracking_number,
            "carrier": order.shipping_carrier,
        }


def _payment_failed(order: Order, env: RenderEnv) -> dict[str, Any]:
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "retry_url": get_tenant_frontend_url(
                f"/account/orders/{order.id}", language=env.language
            ),
        }


def _refund_confirmation(
    refund: PaymentTransaction, env: RenderEnv
) -> dict[str, Any]:
    """The email about ONE refund (a ``PaymentTransaction`` REFUND)."""
    order = refund.order
    with _email_language(order, env.language):
        # What THIS refund paid back and what is still held, so a partial
        # refund does not read as the whole order refunded.
        currency = refund.amount.currency
        still_held = (
            Decimal(0)
            if _is_sample(order)
            else (
                PaymentLedger.captured(order)
                - PaymentLedger.refunded(order)
                - PaymentLedger.charged_back(order)
            )
        )
        return {
            "order_id": order.id,
            "order": order,
            "status": "REFUNDED",
            # A lazy label, resolved by the render under the email's own
            # language: ``get_FOO_display()`` would resolve it at once,
            # under whatever locale the worker last left active, and a
            # customer got the status word in the wrong language.
            "status_display": (
                PaymentStatus(order.payment_status).label
                if order.payment_status
                else PaymentStatus.REFUNDED.label
            ),
            "items": _lines(order),
            "refund_amount": refund.amount,
            "payment_remaining": (
                Money(still_held, currency) if still_held > 0 else None
            ),
        }


def _document_issued(
    document: Any, env: RenderEnv, *, is_credit: bool
) -> dict[str, Any]:
    """The email that carries a registered receipt (or credit) PDF."""
    order = document.order
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "document_number": document.document_number,
            "issue_date": document.issue_date,
            "mydata_mark": document.mydata_mark,
            "is_credit": is_credit,
        }


def _acs_out_for_delivery(shipment: Any, env: RenderEnv) -> dict[str, Any]:
    with _email_language(shipment.order, env.language):
        return {
            "order": shipment.order,
            "shipment": shipment,
            "voucher_no": shipment.voucher_no,
        }


def _boxnow_parcel_at_locker(shipment: Any, env: RenderEnv) -> dict[str, Any]:
    locker = shipment.locker
    with _email_language(shipment.order, env.language):
        return {
            "order": shipment.order,
            "shipment": shipment,
            "locker": locker,
            "locker_address": (
                locker.address_line_1
                if locker is not None
                else shipment.locker_external_id
            ),
            "parcel_id": shipment.parcel_id,
        }


def _new_order_alert(order: Order, env: RenderEnv) -> dict[str, Any]:
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "items": _lines(order),
            "pay_way": order.pay_way,
        }


def _dispute_notification(
    dispute: OrderDispute, env: RenderEnv, *, lost: bool
) -> dict[str, Any]:
    """The store's alert that a provider dispute opened, or (``lost``)
    closed against the store and was recorded as a chargeback."""
    order = dispute.order
    reason = (order.metadata or {}).get("dispute_reason", "unknown")
    chargeback = (
        order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGEBACK,
            provider_reference=dispute.dispute_id,
        ).first()
        if lost and not _is_sample(order)
        else None
    )
    provider_name = (
        payment_provider_label(dispute.provider_code) or dispute.provider_code
    )
    with _email_language(order, env.language):
        return {
            "order_id": order.id,
            "order": order,
            "site": tenant_site_name(),
            "provider": provider_name,
            "dispute_id": dispute.dispute_id,
            "reason": reason,
            "chargeback": chargeback,
            "lost": lost,
            "provider_name": provider_name,
        }


# ---------------------------------------------------------------------------
# Samples: ``sample(env)`` of each type, an unsaved object graph
# ---------------------------------------------------------------------------

SAMPLE_ORDER_ID = 12345


def _sample_order(
    env: RenderEnv,
    *,
    status: str = OrderStatus.PENDING,
    payment_status: str = PaymentStatus.PENDING,
    settlement: str = PaySettlement.ONLINE,
) -> Order:
    """A made-up order that was never saved, with two lines and the totals
    the queries would have annotated."""
    from country.models import Country
    from order.models import Order, OrderItem
    from pay_way.models import PayWay
    from product.models import Product

    codes = [code for code, _name in settings.LANGUAGES]

    pay_way = PayWay(key="STRIPE", settlement=settlement)
    country = Country(alpha_2="GR")
    for code in codes:
        pay_way.set_current_language(code)
        pay_way.description = "Sample payment method"
        pay_way.instructions = "<div>Sample payment instructions.</div>"
        country.set_current_language(code)
        country.name = "Greece"
    pay_way.set_current_language(env.language)
    country.set_current_language(env.language)

    lines = []
    for product_id, (name, price, quantity) in enumerate(
        (
            ("Wireless headphones", Decimal("89.99"), 1),
            ("USB-C cable", Decimal("12.99"), 2),
        ),
        start=1,
    ):
        product = Product(id=product_id, slug=slugify(name))
        for code in codes:
            product.set_current_language(code)
            product.name = name
        product.set_current_language(env.language)
        lines.append(
            OrderItem(
                product=product,
                price=Money(price, "EUR"),
                quantity=quantity,
            )
        )

    order = Order(
        id=SAMPLE_ORDER_ID,
        user_id=SAMPLE_ORDER_ID,
        status=status,
        payment_status=payment_status,
        first_name="Maria",
        last_name="Papadopoulou",
        email=env.recipient.address,
        phone="+302101234567",
        street="Ermou",
        street_number="12",
        city="Athens",
        zipcode="10563",
        country=country,
        pay_way=pay_way,
        language_code=env.language,
        shipping_price=Money(Decimal("4.90"), "EUR"),
        tracking_number="SAMPLE-TRACKING-0001",
        shipping_carrier="ACS",
        created_at=timezone.now() - timedelta(days=1),
        status_updated_at=timezone.now(),
    )
    items_total = sum(line.total_price.amount for line in lines)
    # What ``Order.total_price_items`` reads instead of a query.
    vars(order).update(
        sample_items=lines, items_total=items_total, items_currencies=["EUR"]
    )
    return order


def _sample_refund(env: RenderEnv) -> PaymentTransaction:
    from order.models import PaymentTransaction

    order = _sample_order(env, payment_status=PaymentStatus.COMPLETED)
    return PaymentTransaction(
        order=order,
        kind=PaymentTransactionKind.REFUND,
        amount=Money(Decimal("10.00"), "EUR"),
        provider="stripe",
    )


def _sample_invoice(env: RenderEnv) -> Any:
    from order.models import Invoice

    return Invoice(
        order=_sample_order(env),
        invoice_number="A-00001",
        issue_date=timezone.localdate(),
        mydata_mark=400000000000001,
    )


def _sample_credit(env: RenderEnv) -> Any:
    from order.models import RetailCredit

    return RetailCredit(
        order=_sample_order(env),
        credit_number="CRD-00001",
        issue_date=timezone.localdate(),
        mydata_mark=400000000000002,
    )


def _sample_dispute(env: RenderEnv) -> OrderDispute:
    return OrderDispute(
        order=_sample_order(env, payment_status=PaymentStatus.COMPLETED),
        dispute_id="dp_sample",
        provider_code="stripe",
    )


def _sample_acs_shipment(env: RenderEnv) -> Any:
    from shipping_acs.models import AcsShipment

    return AcsShipment(order=_sample_order(env), voucher_no="7000000001")


def _sample_boxnow_shipment(env: RenderEnv) -> Any:
    from shipping_boxnow.models import BoxNowLocker, BoxNowShipment

    return BoxNowShipment(
        order=_sample_order(env),
        locker=BoxNowLocker(
            external_id="apm-sample",
            name="Locker Stadiou",
            title="Locker Stadiou",
            address_line_1="Stadiou 5",
        ),
        parcel_id="9200000001",
    )


# ---------------------------------------------------------------------------
# The preview's real object for each type
# ---------------------------------------------------------------------------


def _latest_transaction(order: Order, kind: str) -> PaymentTransaction | None:
    return order.payment_transactions.filter(kind=kind).order_by("-pk").first()


def _refund_of(order: Order) -> PaymentTransaction:
    refund = _latest_transaction(order, PaymentTransactionKind.REFUND)
    if refund is None:
        raise NothingToPreview(_("This order has no refund to describe."))
    return refund


def _invoice_of(order: Order) -> Any:
    invoice = getattr(order, "invoice", None)
    if invoice is None:
        raise NothingToPreview(_("This order has no receipt to send."))
    return invoice


def _credit_of(order: Order) -> Any:
    credit = order.credit_documents.order_by("-pk").first()
    if credit is None:
        raise NothingToPreview(_("This order has no credit receipt to send."))
    return credit


def _dispute_of(order: Order) -> OrderDispute:
    chargeback = _latest_transaction(order, PaymentTransactionKind.CHARGEBACK)
    if chargeback is None:
        raise NothingToPreview(_("This order has no dispute to report."))
    return OrderDispute(
        order=order,
        dispute_id=chargeback.provider_reference,
        provider_code=chargeback.provider,
    )


def _shipment_of(related_name: str) -> Callable[[Order], Any]:
    def shipment_of(order: Order) -> Any:
        shipment = getattr(order, related_name, None)
        if shipment is None:
            raise NothingToPreview(_("This order has no such shipment."))
        return shipment

    return shipment_of


#: The object each type is about, found from an order, for the admin
#: preview of a real order. A type absent here is about the order itself.
_PREVIEW_OBJECTS: Mapping[str, Callable[[Order], Any]] = {
    "order.refunded": _refund_of,
    "order.receipt_issued": _invoice_of,
    "order.credit_issued": _credit_of,
    "team.dispute_opened": _dispute_of,
    "team.dispute_lost": _dispute_of,
    "shipping.acs_out_for_delivery": _shipment_of("acs_shipment"),
    "shipping.boxnow_at_locker": _shipment_of("boxnow_shipment"),
}


#: The keys of the types declared in this module: the emails that are about
#: an order, which the admin previews from a chosen order.
ORDER_EMAIL_KEYS: set[str] = set()


def preview_object(key: str, order: Order) -> Any:
    """What the email of type *key* would be about for *order*.

    Raises :class:`NothingToPreview` when the order has no such record, and
    ``KeyError`` for a type this module did not declare (it is not about an
    order: ``ORDER_EMAIL_KEYS``).
    """
    if key not in ORDER_EMAIL_KEYS:
        raise KeyError(key)
    find = _PREVIEW_OBJECTS.get(key)
    return order if find is None else find(order)


# ---------------------------------------------------------------------------
# The types
# ---------------------------------------------------------------------------


def _customer_of_order(order: Order) -> list[Recipient]:
    return [customer_of(order)]


def _customer_of_related_order(obj: Any) -> list[Recipient]:
    """For an object that is about an order (a refund, a document, a
    shipment): the order's customer."""
    return [customer_of(obj.order)]


def _customer_email(
    key: str,
    *,
    templates: str,
    recipients: Callable[[Any], list[Recipient]] = _customer_of_order,
    required: bool = True,
    area: str = "orders",
    **fields: Any,
) -> EmailType:
    ORDER_EMAIL_KEYS.add(key)
    return customer_email(
        key,
        templates=f"{TEMPLATE_DIR}/{templates}",
        recipients=recipients,
        required=required,
        area=area,
        **fields,
    )


def _staff_email(key: str, *, templates: str, **fields: Any) -> EmailType:
    ORDER_EMAIL_KEYS.add(key)
    return staff_email(key, templates=f"{TEMPLATE_DIR}/{templates}", **fields)


_cart = FeatureGate(feature="cart", label=_("Cart feature"))

_customer_email(
    "order.received",
    title=_("Order received"),
    description=_("Sent when an order is placed and its payment is still due."),
    templates="order_received",
    subject=gettext_noop("Order Received - #{order_id}"),
    build_context=_confirmation,
    sample=lambda env: _sample_order(
        env, settlement=PaySettlement.OFFLINE_TRANSFER
    ),
    list_id="order_confirmation",
    gates=(_cart,),
)
_customer_email(
    "order.payment_confirmed",
    title=_("Payment confirmed"),
    description=_("Sent when an order is placed with its payment captured."),
    templates="order_payment_confirmed",
    subject=gettext_noop("Payment Confirmed - Order #{order_id}"),
    build_context=_confirmation,
    sample=lambda env: _sample_order(
        env, payment_status=PaymentStatus.COMPLETED
    ),
    list_id="order_confirmation",
    gates=(_cart,),
)
_customer_email(
    "order.payment_failed",
    title=_("Payment failed"),
    description=_("Sent when an order's payment fails, with a link to retry."),
    templates="payment_failed",
    subject=gettext_noop("Payment Failed - Order #{order_id}"),
    build_context=_payment_failed,
    sample=lambda env: _sample_order(env, payment_status=PaymentStatus.FAILED),
    list_id="payment_failed",
)
_customer_email(
    "order.shipped",
    title=_("Order shipped"),
    description=_("Sent when the parcel is in transit, with its tracking."),
    templates="order_shipped",
    subject=gettext_noop("Your Order #{order_id} Has Shipped"),
    build_context=_order_shipped,
    sample=lambda env: _sample_order(env, status=OrderStatus.SHIPPED),
    list_id="shipping_notification",
)

# Declared before the status emails that share ``order_refunded``: the admin
# preview of a template renders the first type that sends it.
_customer_email(
    "order.refunded",
    title=_("Refund processed"),
    description=_("Sent once for every refund paid back to the customer."),
    templates="order_refunded",
    subject=gettext_noop("Refund Processed - Order #{order_id}"),
    build_context=_refund_confirmation,
    sample=_sample_refund,
    list_id="refund_confirmation",
    recipients=_customer_of_related_order,
)

_STATUS_SUBJECT = gettext_noop(
    "Order #{order_id} Status Update - {status_display}"
)


def _status_email(
    status: OrderStatus,
    *,
    title: Any,
    description: Any,
    templates: str,
    required: bool,
) -> None:
    _customer_email(
        STATUS_EMAIL_KEYS[status],
        title=title,
        description=description,
        templates=templates,
        subject=_STATUS_SUBJECT,
        build_context=lambda order, env: _status_update(order, env, status),
        sample=lambda env: _sample_order(env, status=status),
        list_id="order_status",
        required=required,
    )


_status_email(
    OrderStatus.DELIVERED,
    title=_("Order delivered"),
    description=_("Sent when the carrier reports the parcel delivered."),
    templates="order_delivered",
    required=False,
)
_status_email(
    OrderStatus.COMPLETED,
    title=_("Order completed"),
    description=_("Sent when a delivered, paid order is completed."),
    templates="order_completed",
    required=False,
)
_status_email(
    OrderStatus.CANCELED,
    title=_("Order canceled"),
    description=_("Sent when an order is canceled."),
    templates="order_canceled",
    required=True,
)
_status_email(
    OrderStatus.RETURNED,
    title=_("Order returned"),
    description=_("Sent when an order is returned to the store."),
    templates="order_returned",
    required=True,
)
_status_email(
    OrderStatus.REFUNDED,
    title=_("Order marked refunded"),
    description=_("Sent when an order is closed as refunded."),
    templates="order_refunded",
    required=True,
)

_mydata = StoreSwitchGate(
    setting="MYDATA_ENABLED", label=_("Issuing receipts through myDATA")
)
_customer_email(
    "order.receipt_issued",
    title=_("Receipt issued"),
    description=_("Sent with the PDF of a registered retail receipt."),
    templates="retail_document_issued",
    subject=gettext_noop(
        "Receipt {document_number} for your order #{order_id}"
    ),
    build_context=lambda document, env: _document_issued(
        document, env, is_credit=False
    ),
    sample=_sample_invoice,
    list_id="order_invoice",
    recipients=_customer_of_related_order,
    gates=(_mydata,),
)
_customer_email(
    "order.credit_issued",
    title=_("Credit receipt issued"),
    description=_("Sent with the PDF of a registered retail credit receipt."),
    templates="retail_document_issued",
    subject=gettext_noop(
        "Credit receipt {document_number} for your order #{order_id}"
    ),
    build_context=lambda document, env: _document_issued(
        document, env, is_credit=True
    ),
    sample=_sample_credit,
    list_id="order_credit",
    recipients=_customer_of_related_order,
    gates=(_mydata,),
)

_customer_email(
    "shipping.acs_out_for_delivery",
    title=_("ACS parcel out for delivery"),
    description=_("Sent when ACS has the parcel out for delivery."),
    templates="acs_out_for_delivery",
    subject=gettext_noop("Your ACS parcel is out for delivery"),
    build_context=_acs_out_for_delivery,
    sample=_sample_acs_shipment,
    list_id="acs_out_for_delivery",
    recipients=_customer_of_related_order,
    required=False,
    area="shipping",
    gates=(CarrierGate(carrier="acs", label=_("ACS carrier")),),
)
_customer_email(
    "shipping.boxnow_at_locker",
    title=_("BOX NOW parcel at the locker"),
    description=_(
        "Sent when the parcel arrives at the locker to be collected."
    ),
    templates="boxnow_parcel_at_locker",
    subject=gettext_noop("Your BOX NOW parcel arrived at the locker"),
    build_context=_boxnow_parcel_at_locker,
    sample=_sample_boxnow_shipment,
    list_id="boxnow_at_locker",
    recipients=_customer_of_related_order,
    area="shipping",
    gates=(CarrierGate(carrier="boxnow", label=_("BOX NOW carrier")),),
)

_staff_email(
    "team.new_order",
    title=_("New order alert"),
    description=_("Tells the store's staff that an order was placed."),
    templates="admin_new_order",
    subject=gettext_noop("New order — #{order_id}"),
    build_context=_new_order_alert,
    sample=_sample_order,
)
_staff_email(
    "team.dispute_opened",
    title=_("Dispute opened"),
    description=_("Tells the store's staff that a payment was disputed."),
    templates="dispute_notification",
    subject=gettext_noop(
        "[{site}] {provider} dispute opened — Order #{order_id}"
    ),
    build_context=lambda dispute, env: _dispute_notification(
        dispute, env, lost=False
    ),
    sample=_sample_dispute,
)
_staff_email(
    "team.dispute_lost",
    title=_("Dispute lost"),
    description=_("Tells the store's staff that a dispute closed against it."),
    templates="dispute_notification",
    subject=gettext_noop(
        "[{site}] {provider} dispute lost — Order #{order_id}"
    ),
    build_context=lambda dispute, env: _dispute_notification(
        dispute, env, lost=True
    ),
    sample=_sample_dispute,
)


@dataclass(frozen=True, slots=True)
class PaymentAfterCancel:
    """A confirmed payment that arrived for an order already canceled."""

    order_id: int
    payment_id: str
    payment_method: str


@dataclass(frozen=True, slots=True)
class PaymentMismatch:
    """A confirmed charge whose amount is not the order's."""

    order_id: int
    payment_id: str
    provider: str
    observed: object
    expected: object


@dataclass(frozen=True, slots=True)
class RefundFailed:
    """A refund the provider accepted and later failed."""

    order_id: int
    provider: str
    refund_id: str
    amount: object
    reason: str


def _payment_after_cancel(
    alert: PaymentAfterCancel, env: RenderEnv
) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "order_id": alert.order_id,
        "payment_id": alert.payment_id,
        "provider": alert.payment_method,
    }


def _payment_mismatch(alert: PaymentMismatch, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "order_id": alert.order_id,
        "payment_id": alert.payment_id,
        "provider": alert.provider,
        "observed": alert.observed,
        "expected": alert.expected,
    }


def _refund_failed(alert: RefundFailed, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "order_id": alert.order_id,
        "provider": alert.provider,
        "refund_id": alert.refund_id,
        "amount": alert.amount,
        "reason": alert.reason,
    }


OPS_TEMPLATE_DIR = "emails/ops"

staff_email(
    "ops.payment_after_cancel",
    area="ops",
    title=_("Payment received for a canceled order"),
    description=_(
        "Tells the store's staff that a payment arrived for an order that "
        "was already canceled, so the customer must be refunded."
    ),
    templates=f"{OPS_TEMPLATE_DIR}/payment_after_cancel",
    subject=gettext_noop(
        "[{site}] Payment received for canceled order {order_id}"
    ),
    build_context=_payment_after_cancel,
    sample=lambda env: PaymentAfterCancel(
        order_id=1234, payment_id="pi_3QxExample", payment_method="stripe"
    ),
)
staff_email(
    "ops.payment_mismatch",
    area="ops",
    title=_("Payment that does not match its order"),
    description=_(
        "Tells the store's staff that a confirmed payment collected a "
        "different amount than its order is for."
    ),
    templates=f"{OPS_TEMPLATE_DIR}/payment_mismatch",
    subject=gettext_noop("[{site}] Payment does not match order {order_id}"),
    build_context=_payment_mismatch,
    sample=lambda env: PaymentMismatch(
        order_id=1234,
        payment_id="pi_3QxExample",
        provider="Stripe",
        observed=Money(Decimal("40.00"), "EUR"),
        expected=Money(Decimal("45.50"), "EUR"),
    ),
)
staff_email(
    "ops.refund_failed",
    area="ops",
    title=_("A refund the provider accepted failed"),
    description=_(
        "Tells the store's staff that a refund failed after the provider "
        "accepted it, so the customer has not been paid back."
    ),
    templates=f"{OPS_TEMPLATE_DIR}/refund_failed",
    subject=gettext_noop("[{site}] Refund failed for order {order_id}"),
    build_context=_refund_failed,
    sample=lambda env: RefundFailed(
        order_id=1234,
        provider="Stripe",
        refund_id="re_3QxExample",
        amount=Money(Decimal("45.50"), "EUR"),
        reason="expired_or_canceled_card",
    ),
)
