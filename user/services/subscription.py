"""Creating subscriptions — the one place a subscription comes into being.

Two entry points, one per kind of subscriber:

- :func:`subscribe_account` — a signed-in account (the topic subscribe
  action, the account's subscriptions endpoint, the bulk update, and the
  default topics at signup). A topic that requires confirmation gets a
  PENDING row with a live confirmation link and its email; any other
  topic an ACTIVE row. No path can leave a PENDING row without a token,
  or turn a confirmation-required topic ACTIVE without the click.

- :func:`subscribe_email_to_newsletter` — the anonymous newsletter form,
  run from ``user.tasks.subscribe_to_newsletter_task`` so the request
  never touches the rows (see the view for why).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from allauth.account.models import EmailAddress
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from user.models.subscription import SubscriptionTopic, UserSubscription

logger = logging.getLogger(__name__)

#: A resubmission of the newsletter form inside this window re-sends
#: nothing: the link already on its way is the answer, and the form
#: cannot be used to flood one inbox.
NEWSLETTER_RESEND_COOLDOWN = timedelta(minutes=10)

Status = UserSubscription.SubscriptionStatus


class SubscribeOutcome(models.TextChoices):
    SUBSCRIBED = "subscribed", _("Subscribed")
    PENDING_CONFIRMATION = "pending_confirmation", _("Pending confirmation")
    ALREADY_ACTIVE = "already_active", _("Already subscribed")
    ALREADY_PENDING = "already_pending", _("Already pending confirmation")


@dataclass(frozen=True)
class SubscribeResult:
    subscription: UserSubscription
    outcome: SubscribeOutcome
    #: True when the row did not exist before this call.
    created: bool


def dispatch_confirmation(subscription: UserSubscription) -> None:
    """Queue the confirmation email for an armed row, after commit, in
    the row's own schema (``dispatch_on_commit`` pins it now)."""
    from tenant.celery import dispatch_on_commit
    from user.tasks import send_subscription_confirmation_email_task

    dispatch_on_commit(
        send_subscription_confirmation_email_task, [subscription.id]
    )


def subscribe_account(
    user,
    topic: SubscriptionTopic,
    *,
    source: str = UserSubscription.Source.ACCOUNT,
    language: str = "",
) -> SubscribeResult:
    """Subscribe ``user`` to ``topic``, honouring its confirmation rule.

    An ACTIVE or PENDING row is returned unchanged with the matching
    ``ALREADY_*`` outcome — a pending one is re-sent only on request
    (the admin's "Resend confirmation"). A new row, or an UNSUBSCRIBED
    or BOUNCED one, becomes PENDING with a fresh link and its email when
    the topic requires confirmation, and ACTIVE otherwise.
    """
    with transaction.atomic():
        subscription, created = (
            UserSubscription.objects.select_for_update().get_or_create(
                user=user,
                topic=topic,
                defaults={"source": source, "language": language},
            )
        )
        if not created:
            if subscription.status == Status.ACTIVE:
                return SubscribeResult(
                    subscription, SubscribeOutcome.ALREADY_ACTIVE, False
                )
            if subscription.status == Status.PENDING:
                return SubscribeResult(
                    subscription, SubscribeOutcome.ALREADY_PENDING, False
                )
            subscription.source = source
            subscription.language = language or subscription.language

        if topic.requires_confirmation:
            subscription.arm_confirmation()
            subscription.save()
            dispatch_confirmation(subscription)
            return SubscribeResult(
                subscription, SubscribeOutcome.PENDING_CONFIRMATION, created
            )

        subscription.status = Status.ACTIVE
        subscription.unsubscribed_at = None
        subscription.save()
        return SubscribeResult(
            subscription, SubscribeOutcome.SUBSCRIBED, created
        )


def _needs_arming(subscription: UserSubscription) -> bool:
    match subscription.status:
        case Status.ACTIVE:
            return False
        case Status.PENDING:
            sent_at = subscription.confirmation_sent_at
            return (
                sent_at is None
                or timezone.now() - sent_at >= NEWSLETTER_RESEND_COOLDOWN
            )
        case _:
            # UNSUBSCRIBED / BOUNCED: a new, explicit request — a fresh
            # link and a fresh consent record.
            return True


def subscribe_email_to_newsletter(
    *,
    topic: SubscriptionTopic,
    email: str,
    consent_text: str,
    consent_ip: str | None,
    consent_user_agent: str,
    language: str,
    consented_at: datetime,
) -> UserSubscription | None:
    """Create or re-arm the PENDING row for a newsletter-form submission.

    Returns the armed row (its confirmation email is the caller's to
    send), or ``None`` when there is nothing to send: the address is
    already confirmed, a link went out inside the cooldown, or a
    concurrent submission for the same address won the insert.

    An address that a VERIFIED account owns is subscribed as that
    account — still double opt-in. The ``consent_*`` values are the
    snapshot the request took; they replace the row's evidence only
    when the row is (re-)armed, since only then was consent asked for.
    """
    owner = (
        EmailAddress.objects.filter(email__iexact=email, verified=True)
        .values_list("user_id", flat=True)
        .first()
    )
    try:
        with transaction.atomic():
            rows = UserSubscription.objects.select_for_update()
            if owner is not None:
                rows = rows.filter(user_id=owner)
            else:
                rows = rows.filter(user__isnull=True, email__iexact=email)
            subscription = rows.filter(topic=topic).first()

            if subscription is not None and not _needs_arming(subscription):
                return None
            if subscription is None:
                subscription = UserSubscription(user_id=owner, topic=topic)

            subscription.arm_confirmation()
            subscription.email = email
            subscription.source = UserSubscription.Source.NEWSLETTER_FORM
            subscription.language = language
            subscription.consent_text = consent_text
            subscription.consent_ip = consent_ip
            subscription.consent_user_agent = consent_user_agent
            subscription.consented_at = consented_at
            subscription.save()
            return subscription
    except IntegrityError:
        # A concurrent submission for the same address created the row
        # between our read and our insert; its email covers this one.
        logger.info(
            "newsletter: concurrent signup for topic %s collapsed", topic.pk
        )
        return None
