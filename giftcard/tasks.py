import logging

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core import celery_app
from core.tasks import MonitoredTask
from core.utils.email_context import build_email_context
from tenant.credentials import tenant_from_email, tenant_site_name

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
        card = GiftCard.objects.get(id=gift_card_id)
    except GiftCard.DoesNotExist:
        logger.error("Gift card %s not found for delivery", gift_card_id)
        return {"status": "error", "reason": "not_found"}

    if card.delivered_at is not None:
        return {"status": "skipped", "reason": "already_delivered"}
    if not card.recipient_email:
        return {"status": "skipped", "reason": "no_recipient"}

    context = build_email_context(card=card, balance=card.balance)
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
        deliver_gift_card_email.apply(args=[card.id])
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
def credit_refund_to_gift_cards(self, order_id: int) -> dict:
    """Return the gift-card-settled portion of a refunded order to the
    source card(s)."""
    from giftcard.services import GiftCardService
    from order.models.order import Order

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        logger.error("Order %s not found for gift-card refund", order_id)
        return {"status": "error", "reason": "order_not_found"}

    credited = GiftCardService.credit_refund(order)
    return {"status": "success", "credited": str(credited)}
