from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db import transaction

from core.utils.tenant_urls import get_tenant_frontend_url
from emails.send import send_email, was_sent
from user.models.subscription import UserSubscription

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from user.models.account import UserAccount as User
else:
    User = get_user_model()


def subscription_confirmation_url(token: str, *, language: str) -> str:
    """The storefront page that confirms a pending subscription.

    A STOREFRONT page, not the API endpoint, built the way every other
    storefront link in outbound mail is: against the tenant's primary
    domain (``get_tenant_frontend_url``). The page shows a button that
    POSTs the token — a mail link scanner prefetching the URL must not
    confirm anything, which is why ``ConfirmSubscriptionByTokenView``
    accepts POST only.
    """
    return get_tenant_frontend_url(
        f"/newsletter/confirm/{token}", language=language
    )


def send_subscription_confirmation(subscription: UserSubscription) -> bool:
    """Email the double opt-in link for a PENDING subscription.

    Works for account and guest rows alike: the recipient is
    ``subscription.recipient_email`` and the language the one the
    subscription was made in (an account row that recorded none uses
    the account's language).

    Returns False when there is nothing to send (the row is not pending, or
    the type is off for the store). A failure to SEND is
    raised, not swallowed: ``send_subscription_confirmation_email_task``
    retries on it, and a helper that caught it made that retry policy
    unreachable.
    """
    if subscription.status != UserSubscription.SubscriptionStatus.PENDING:
        logger.warning(
            "Attempted to send confirmation for non-pending subscription %s",
            subscription.id,
        )
        return False

    if not subscription.confirmation_token:
        logger.error(
            "No confirmation token for subscription %s", subscription.id
        )
        return False

    if not was_sent(send_email("newsletter.confirmation", subscription)):
        return False

    logger.info("Sent confirmation email for subscription %s", subscription.id)
    return True


def send_already_subscribed_notice(subscription: UserSubscription) -> bool:
    """Email the owner of a confirmed subscription that it already exists.

    The answer to a newsletter-form submission for an address that is
    already ACTIVE: the form answers every visitor the same way, so this
    mail is the only place the address's owner learns it.

    Returns False when there is nothing to send (the row is no longer
    active, or the type is off for the store); a failure to SEND is
    raised for the task's retry policy.
    """
    if subscription.status != UserSubscription.SubscriptionStatus.ACTIVE:
        logger.warning(
            "Attempted to send the already-subscribed notice for "
            "non-active subscription %s",
            subscription.id,
        )
        return False
    if not was_sent(send_email("newsletter.already_subscribed", subscription)):
        return False
    logger.info(
        "Sent already-subscribed notice for subscription %s", subscription.id
    )
    return True


def claim_guest_subscriptions(user: User, email: str) -> int:
    """Attach the guest subscriptions made with ``email`` to ``user``.

    Call ONLY once ``email`` is a verified address of ``user``: a guest
    row carries its subscriber's consent record, and an unverified
    address may belong to someone else. Where the account already has a
    row for the same topic, the ACTIVE one wins and the other is
    deleted (neither active: the account's own row stays).

    Returns the number of guest rows attached.
    """
    active = UserSubscription.SubscriptionStatus.ACTIVE
    claimed = 0
    with transaction.atomic():
        guests = UserSubscription.objects.select_for_update().filter(
            user__isnull=True, email__iexact=email
        )
        for guest in guests:
            existing = (
                UserSubscription.objects.select_for_update()
                .filter(user=user, topic_id=guest.topic_id)
                .first()
            )
            if existing is not None:
                if guest.status == active and existing.status != active:
                    existing.delete()
                else:
                    guest.delete()
                    continue
            guest.user = user
            guest.save(update_fields=["user", "updated_at"])
            claimed += 1
    if claimed:
        logger.info(
            "Claimed %s guest subscription(s) for user %s", claimed, user.pk
        )
    return claimed
