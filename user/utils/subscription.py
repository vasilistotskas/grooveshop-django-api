from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection, transaction

from core.utils.tenant_urls import (
    get_tenant_api_base_url,
    get_tenant_frontend_url,
)
from emails.send import send_email, was_sent
from user.models.subscription import UserSubscription

logger = logging.getLogger(__name__)

# Unsubscribe links are signed with ``django.core.signing`` under a
# dedicated salt (NOT the password-reset generator) so the token is
# purpose-scoped, tamper-proof, and — crucially — does NOT invalidate when
# the user logs in or changes their password. ``UNSUBSCRIBE_MAX_AGE`` gives
# the link a long life so a newsletter that sits in an inbox for months
# still honours one-click unsubscribe (RFC 8058), while still bounding the
# validity of a leaked token.
UNSUBSCRIBE_SALT = "user.unsubscribe"
UNSUBSCRIBE_MAX_AGE = timedelta(days=365)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

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


def _make_unsubscribe_token(user: AbstractBaseUser) -> str:
    """Sign the user's pk + owning schema into a tamper-proof token.

    The schema is baked in because ``SECRET_KEY`` is global and the
    user table exists in every tenant schema — a pk-only token minted
    for tenant A's user 42 would verify on tenant B's domain and
    silently unsubscribe tenant B's user 42. The verifier rejects any
    token whose schema does not match ``connection.schema_name``.
    """
    return signing.dumps(
        {"schema": connection.schema_name, "pk": user.pk},
        salt=UNSUBSCRIBE_SALT,
    )


def _make_guest_unsubscribe_token(subscription: UserSubscription) -> str:
    """Sign a guest subscription's uuid + owning schema.

    A guest has no account, so the token names the subscription row
    itself (``sid``). Schema-scoped for the same reason as
    ``_make_unsubscribe_token``: the verifier rejects a token minted
    for another tenant.
    """
    return signing.dumps(
        {"schema": connection.schema_name, "sid": str(subscription.uuid)},
        salt=UNSUBSCRIBE_SALT,
    )


def generate_unsubscribe_link(subscription: UserSubscription) -> str:
    """Topic-scoped unsubscribe URL for one subscription's recipient.

    An account row signs the account (the link then unsubscribes that
    account from the topic); a guest row signs the row itself. The URL
    targets a Django API endpoint that has no Nuxt proxy, so the
    tenant's API origin is the correct base.
    """
    token = (
        _make_unsubscribe_token(subscription.user)
        if subscription.user_id
        else _make_guest_unsubscribe_token(subscription)
    )
    base_url = get_tenant_api_base_url()
    return (
        f"{base_url}/api/v1/user/unsubscribe/{token}/{subscription.topic.slug}"
    )


def generate_blanket_unsubscribe_link(
    user: AbstractBaseUser,
) -> str:
    """Build the blanket (no-topic) unsubscribe URL for ``user``.

    Mirrors ``generate_unsubscribe_link`` but emits the URL form that
    ``UnsubscribeAllView`` accepts — drops every active subscription
    when the recipient hits one-click unsubscribe in Gmail/Outlook.

    Use this in marketing/notification emails that aren't bound to a
    single topic (re-engagement, product alerts) so the email still
    carries an RFC 8058-compliant ``List-Unsubscribe`` header.

    Accepts ``AbstractBaseUser`` so callers with a generic FK
    (e.g. ``ProductAlert.user``) can pass it directly — only ``pk`` is
    required.
    """
    # Django API endpoint — see note on ``generate_unsubscribe_link``.
    token = _make_unsubscribe_token(user)
    base_url = get_tenant_api_base_url()
    return f"{base_url}/api/v1/user/unsubscribe/{token}"
