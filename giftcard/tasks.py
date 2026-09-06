import logging

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# See order/tasks.py for rationale — eager gettext over lazy for email
# subjects: the string must resolve INSIDE the recipient-language
# override, not whenever the lazy proxy happens to be formatted.
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from core import celery_app
from core.tasks import MonitoredTask
from core.utils.email_context import build_email_context
from core.utils.i18n import get_user_language
from tenant.credentials import (
    tenant_contact_email,
    tenant_from_email,
    tenant_site_name,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def deliver_gift_card_email(self, gift_card_id: int) -> dict:
    """Email the gift card to its recipient. Idempotent via
    ``delivered_at`` — retries and the daily scheduled sweep both
    funnel through here."""
    from giftcard.models import GiftCard

    try:
        card = GiftCard.objects.select_related("issued_to").get(id=gift_card_id)
    except GiftCard.DoesNotExist:
        logger.error("Gift card %s not found for delivery", gift_card_id)
        return {"status": "error", "reason": "not_found"}

    if card.delivered_at is not None:
        return {"status": "skipped", "reason": "already_delivered"}
    if not card.recipient_email:
        return {"status": "skipped", "reason": "no_recipient"}

    context = build_email_context(card=card, balance=card.balance)
    with translation.override(get_user_language(card.issued_to)):
        subject = _("[{site}] You received a gift card!").format(
            site=tenant_site_name()
        )
        text_content = render_to_string(
            "emails/giftcard/gift_card_delivery.txt", context
        )
        html_content = render_to_string(
            "emails/giftcard/gift_card_delivery.html", context
        )
    msg = EmailMultiAlternatives(
        subject,
        text_content,
        tenant_from_email(),
        [card.recipient_email],
        reply_to=[tenant_contact_email()],
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()

    card.delivered_at = timezone.now()
    card.save(update_fields=["delivered_at"])
    logger.info("Delivered gift card %s", card.code)
    return {"status": "success", "gift_card_id": card.id}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def deliver_scheduled_gift_cards(self) -> dict:
    """Daily sweep: send cards whose ``deliver_at`` has arrived."""
    from giftcard.models import GiftCard

    due = GiftCard.objects.filter(
        delivered_at__isnull=True,
        deliver_at__isnull=False,
        deliver_at__lte=timezone.now(),
    ).exclude(recipient_email="")
    sent = 0
    for card in due:
        # .delay(), never .apply(): the eager path does NOT carry the
        # ``_schema_name`` header that ``TenantTask`` stamps, so the
        # delivery would run against the public schema — where the
        # giftcard table does not exist — and the failure would be
        # swallowed into an EagerResult (CELERY_TASK_EAGER_PROPAGATES is
        # False in production), leaving the card permanently undelivered
        # while this sweep still reported success.
        deliver_gift_card_email.delay(card.id)
        sent += 1
    return {"status": "success", "sent": sent}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_gift_card_purchase_receipt(self, purchase_id: int) -> dict:
    """Confirmation email to the BUYER after payment lands.

    Not a tax document — a multi-purpose voucher sale is outside VAT
    scope, so this is a plain purchase confirmation (the recipient
    gets the card itself via ``deliver_gift_card_email``).
    """
    from giftcard.enum import GiftCardPurchaseStatus
    from giftcard.models import GiftCardPurchase

    try:
        purchase = GiftCardPurchase.objects.select_related("buyer").get(
            id=purchase_id
        )
    except GiftCardPurchase.DoesNotExist:
        logger.error("Gift card purchase %s not found", purchase_id)
        return {"status": "error", "reason": "not_found"}

    if purchase.status != GiftCardPurchaseStatus.PAID:
        return {"status": "skipped", "reason": "not_paid"}
    if not purchase.buyer_email:
        return {"status": "skipped", "reason": "no_buyer_email"}

    context = build_email_context(purchase=purchase)
    with translation.override(get_user_language(purchase.buyer)):
        subject = _("[{site}] Your gift card purchase").format(
            site=tenant_site_name()
        )
        text_content = render_to_string(
            "emails/giftcard/gift_card_purchase_receipt.txt", context
        )
        html_content = render_to_string(
            "emails/giftcard/gift_card_purchase_receipt.html", context
        )
    msg = EmailMultiAlternatives(
        subject,
        text_content,
        tenant_from_email(),
        [purchase.buyer_email],
        reply_to=[tenant_contact_email()],
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()
    logger.info("Sent gift card purchase receipt for %s", purchase.uuid)
    return {"status": "success", "purchase_id": purchase.id}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def send_gift_card_expiry_reminders(self) -> dict:
    """Daily sweep: warn recipients whose card expires soon.

    Window is merchant-tunable (``GIFT_CARD_EXPIRY_REMINDER_DAYS``,
    0 disables); each card is reminded exactly once via
    ``expiry_reminder_sent_at``.
    """
    from datetime import timedelta

    from extra_settings.models import Setting

    from giftcard.enum import GiftCardStatus
    from giftcard.models import GiftCard

    days = int(Setting.get("GIFT_CARD_EXPIRY_REMINDER_DAYS", default=30) or 0)
    if days <= 0:
        return {"status": "skipped", "reason": "disabled"}

    now = timezone.now()
    window_end = now + timedelta(days=days)
    due = (
        GiftCard.objects.filter(
            status=GiftCardStatus.ACTIVE,
            expires_at__isnull=False,
            expires_at__gt=now,
            expires_at__lte=window_end,
            expiry_reminder_sent_at__isnull=True,
        )
        .exclude(recipient_email="")
        .select_related("issued_to")
    )

    sent = 0
    for card in due:
        if card.balance.amount <= 0:
            continue
        context = build_email_context(card=card, balance=card.balance)
        with translation.override(get_user_language(card.issued_to)):
            subject = _("[{site}] Your gift card expires soon").format(
                site=tenant_site_name()
            )
            text_content = render_to_string(
                "emails/giftcard/gift_card_expiry_reminder.txt", context
            )
            html_content = render_to_string(
                "emails/giftcard/gift_card_expiry_reminder.html", context
            )
        msg = EmailMultiAlternatives(
            subject,
            text_content,
            tenant_from_email(),
            [card.recipient_email],
            reply_to=[tenant_contact_email()],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()
        card.expiry_reminder_sent_at = timezone.now()
        card.save(update_fields=["expiry_reminder_sent_at"])
        sent += 1

    return {"status": "success", "sent": sent}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def expire_gift_cards(self) -> dict:
    """Daily sweep: EXPIRE-out the remaining balance of expired cards."""
    from giftcard.services import GiftCardService

    expired = GiftCardService.expire_cards()
    return {"status": "success", "expired": expired}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def credit_refund_to_gift_cards(
    self, order_id: int, amount: str | None = None
) -> dict:
    """Return the refunded gift-card value to the source card(s).

    *amount* is a decimal STRING (or None for a full refund) because a
    Celery argument has to survive JSON — `Money` and `Decimal` do not.
    """
    from decimal import Decimal, InvalidOperation

    from giftcard.services import GiftCardService
    from order.models.order import Order

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for gift-card refund", order_id)
        return {"status": "error", "reason": "order_not_found"}

    refunded = None
    if amount is not None:
        try:
            refunded = Decimal(amount)
        except InvalidOperation, TypeError, ValueError:
            # Crediting the full redemption on an unreadable amount is
            # how money gets created; refuse instead.
            logger.error(
                "Gift-card refund for order %s carries an unreadable "
                "amount %r — refusing to credit rather than guessing",
                order_id,
                amount,
            )
            return {"status": "error", "reason": "bad_amount"}

    credited = GiftCardService.credit_refund(order, refunded)
    return {"status": "success", "credited": str(credited)}
