"""The gift card emails: their types, builders and samples.

``emails.EmailsConfig`` imports this module at boot. What stays with the
tasks (``giftcard.tasks``) is choosing WHEN to send: the delivery and
reminder claims and the bookkeeping after a send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.utils.translation import gettext, gettext_noop
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from core.utils.i18n import get_user_language
from emails.declare import customer_email, staff_email
from emails.gates import FeatureGate
from emails.recipients import Recipient
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

if TYPE_CHECKING:
    from giftcard.models import GiftCard, GiftCardPurchase

TEMPLATE_DIR = "emails/giftcard"


@dataclass(frozen=True, slots=True)
class GiftCardReversal:
    """A gift card purchase whose payment went wrong. *unrecovered* is the
    value of the card already spent when the provider reversed the payment;
    ``None`` means the payment is only disputed, and nothing is voided yet."""

    purchase: GiftCardPurchase
    unrecovered: Decimal | None = None

    @property
    def disputed(self) -> bool:
        return self.unrecovered is None


def _card(card: GiftCard, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "card": card,
        "balance": card.balance,
    }


def _purchase(purchase: GiftCardPurchase, env: RenderEnv) -> dict[str, Any]:
    return {"site": tenant_site_name(), "purchase": purchase}


def _card_recipient(card: GiftCard) -> list[Recipient]:
    """The card's recipient, in the language of whoever issued it."""
    if not card.recipient_email:
        return []
    return [
        Recipient(
            address=card.recipient_email,
            language=get_user_language(card.issued_to),
        )
    ]


def _buyer_recipient(purchase: GiftCardPurchase) -> list[Recipient]:
    if not purchase.buyer_email:
        return []
    return [
        Recipient(
            address=purchase.buyer_email,
            language=get_user_language(purchase.buyer),
        )
    ]


def _sample_card(env: RenderEnv) -> GiftCard:
    from giftcard.models import GiftCard

    card = GiftCard(
        code="GC-SAMPLE-0001",
        initial_value=Money(Decimal("50.00"), "EUR"),
        recipient_email=env.recipient.address,
        recipient_name="Giorgos",
        sender_name="Maria",
        message="Happy birthday!",
        expires_at=datetime(2030, 1, 31, 12, 0, tzinfo=UTC),
    )
    # What ``GiftCard.balance`` reads instead of querying the ledger.
    vars(card)["ledger_total"] = Decimal("50.00")
    return card


def _sample_purchase(env: RenderEnv) -> GiftCardPurchase:
    from giftcard.models import GiftCardPurchase

    return GiftCardPurchase(
        buyer_email=env.recipient.address,
        amount=Money(Decimal("50.00"), "EUR"),
        recipient_email="recipient@example.com",
        recipient_name="Giorgos",
        sender_name="Maria",
        message="Happy birthday!",
        deliver_at=datetime(2030, 1, 1, 9, 0, tzinfo=UTC),
    )


def _reversal(alert: GiftCardReversal, env: RenderEnv) -> dict[str, Any]:
    purchase = alert.purchase
    disputed = alert.disputed
    return {
        "site": tenant_site_name(),
        "disputed": disputed,
        # The two outcomes share one type, one switch and one template.
        "headline": (
            gettext("Gift card payment disputed")
            if disputed
            else gettext("Gift card spent before its payment was reversed")
        ),
        "purchase_uuid": purchase.uuid,
        "amount": purchase.amount,
        "unrecovered": (
            None
            if disputed
            else Money(alert.unrecovered, purchase.amount.currency)
        ),
    }


def _sample_reversal(env: RenderEnv) -> GiftCardReversal:
    from giftcard.models import GiftCardPurchase

    return GiftCardReversal(
        purchase=GiftCardPurchase(
            uuid=UUID("00000000-0000-4000-8000-0000000000e1"),
            amount=Money(Decimal("50.00"), "EUR"),
        ),
        unrecovered=Decimal("20.00"),
    )


_gift_cards = FeatureGate(feature="gift_cards", label=_("Gift cards feature"))

customer_email(
    "giftcard.delivery",
    title=_("Gift card delivery"),
    description=_(
        "Sent to the person a gift card was given to, with its code."
    ),
    area="gift_cards",
    templates=f"{TEMPLATE_DIR}/gift_card_delivery",
    subject=gettext_noop("[{site}] You received a gift card!"),
    build_context=_card,
    sample=_sample_card,
    recipients=_card_recipient,
    gates=(_gift_cards,),
)
customer_email(
    "giftcard.purchase_receipt",
    title=_("Gift card purchase confirmation"),
    description=_("Sent to the buyer of a gift card once the payment lands."),
    area="gift_cards",
    templates=f"{TEMPLATE_DIR}/gift_card_purchase_receipt",
    subject=gettext_noop("[{site}] Your gift card purchase"),
    build_context=_purchase,
    sample=_sample_purchase,
    recipients=_buyer_recipient,
    gates=(_gift_cards,),
)
customer_email(
    "giftcard.expiry_reminder",
    title=_("Gift card expiry reminder"),
    description=_("Sent once, shortly before an unused gift card expires."),
    area="gift_cards",
    templates=f"{TEMPLATE_DIR}/gift_card_expiry_reminder",
    subject=gettext_noop("[{site}] Your gift card expires soon"),
    build_context=_card,
    sample=_sample_card,
    recipients=_card_recipient,
    required=False,
    gates=(_gift_cards,),
)
staff_email(
    "ops.gift_card_reversal",
    area="ops",
    title=_("Gift card whose payment was reversed or disputed"),
    description=_(
        "Tells the store's staff that the payment of a gift card purchase "
        "was disputed, or reversed after the card was partly spent."
    ),
    templates="emails/ops/gift_card_reversal",
    subject=gettext_noop("[{site}] {headline}"),
    build_context=_reversal,
    sample=_sample_reversal,
    # Not gated on the gift cards feature: a store that switched it off
    # can still have a sold card whose payment is reversed or disputed.
)
