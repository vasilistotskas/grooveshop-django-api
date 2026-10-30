"""Creating subscriptions — the one place a subscription comes into being.

Two entry points, one per kind of subscriber:

- :func:`subscribe_account` — a signed-in account (the topic subscribe
  action, the bulk update, and the default topics at signup). A topic that requires confirmation gets a
  PENDING row with a live confirmation link and its email; any other
  topic an ACTIVE row. No path can leave a PENDING row without a token,
  or turn a confirmation-required topic ACTIVE without the click.

- :func:`subscribe_email_to_newsletter` — the anonymous newsletter form,
  run from ``user.tasks.subscribe_to_newsletter_task`` so the request
  never touches the rows (see the view for why).

:func:`ensure_default_newsletter_topic` gives a store the topic that
form subscribes to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from allauth.account.models import EmailAddress
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from emails.preferences import offered_types
from emails.registry import registry
from emails.types import EmailType
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


def opt_out_of_marketing(
    address: str,
    *,
    account_pk: int | None,
    source: EmailOptOut.Source,
) -> bool:
    """Unsubscribe *address* from every marketing email: the one writer.

    Writes ``EmailOptOut`` for every marketing type, which is what stops the
    mail, and, for an account, ``marketing_opted_out_at`` (account state the
    admin shows), in one transaction. A guest has no account: *account_pk*
    is None. Both the blanket unsubscribe link and the customer admin come
    here, so neither half is ever written alone.

    Returns True when this call recorded the account's flag, False when it
    was already set (the first time stands), there is no account, or the
    account does not exist.
    """
    from user.models.account import UserAccount

    with transaction.atomic():
        optout.opt_out(address, EVERY_MARKETING_TYPE, source=source)
        if account_pk is None:
            return False
        return bool(
            UserAccount.objects.filter(
                pk=account_pk, marketing_opted_out_at__isnull=True
            ).update(marketing_opted_out_at=timezone.now())
        )


@dataclass(frozen=True)
class MarketingPreference:
    email_type: EmailType
    #: False while an opt-out row covers the type (its own or ``*``).
    enabled: bool


def marketing_preferences(address: str) -> list[MarketingPreference]:
    """The marketing types the store offers, each with whether *address*
    still receives it."""
    declined = set(
        EmailOptOut.objects.filter(
            address_hash=optout.address_hash(address)
        ).values_list("type_key", flat=True)
    )
    every = EVERY_MARKETING_TYPE in declined
    return [
        MarketingPreference(
            email_type=email_type,
            enabled=not every and email_type.key not in declined,
        )
        for email_type in offered_types()
    ]


def _allow_marketing_type(hashed: str, type_key: str) -> None:
    """Lift the opt-out that covers *type_key*, and nothing else.

    A ``*`` row is replaced by one row per OTHER marketing type, in the
    source the ``*`` row had, so the customer's other choices stand.
    """
    EmailOptOut.objects.filter(address_hash=hashed, type_key=type_key).delete()
    every = EmailOptOut.objects.filter(
        address_hash=hashed, type_key=EVERY_MARKETING_TYPE
    ).first()
    if every is None:
        return
    every.delete()
    EmailOptOut.objects.bulk_create(
        EmailOptOut(address_hash=hashed, type_key=key, source=every.source)
        for key in (t.key for t in registry.all_types() if t.opt_out_applies)
        if key != type_key
    )


def set_marketing_preferences(
    address: str,
    *,
    account_pk: int,
    choices: dict[str, bool],
) -> list[MarketingPreference]:
    """Apply a customer's choices for their own *address*: the one writer
    of a lifted opt-out.

    ``choices`` maps a marketing type key to whether the customer wants it.
    Declining records a per-type row, accepting lifts the row that covers
    the type (see :func:`_allow_marketing_type`). When no row is left the
    account's ``marketing_opted_out_at`` is cleared as well, in the same
    transaction, so the flag and the rows never disagree about "none".
    Staff never come through here: they record, only the customer lifts.
    """
    from user.models.account import UserAccount

    hashed = optout.address_hash(address)
    with transaction.atomic():
        # One writer per account at a time: two requests splitting the
        # same ``*`` row would otherwise both recreate the others.
        UserAccount.objects.select_for_update().get(pk=account_pk)
        for type_key, enabled in choices.items():
            if enabled:
                _allow_marketing_type(hashed, type_key)
            elif not EmailOptOut.objects.filter(
                address_hash=hashed,
                type_key__in=[type_key, EVERY_MARKETING_TYPE],
            ).exists():
                optout.opt_out(
                    address, type_key, source=EmailOptOut.Source.ACCOUNT
                )
        if not EmailOptOut.objects.filter(address_hash=hashed).exists():
            UserAccount.objects.filter(
                pk=account_pk, marketing_opted_out_at__isnull=False
            ).update(marketing_opted_out_at=None)
    return marketing_preferences(address)


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


#: The topic a store starts with, so the newsletter band that the
#: default page layout carries has something to subscribe to. Written in
#: every storefront language; the merchant renames or retires it in the
#: admin like any other topic.
DEFAULT_NEWSLETTER_TOPIC_SLUG = "newsletter"
DEFAULT_NEWSLETTER_TOPIC_TRANSLATIONS = {
    "el": {
        "name": "Newsletter",
        "description": "Νέα προϊόντα και προσφορές του καταστήματος.",
    },
    "en": {
        "name": "Newsletter",
        "description": "New products and offers from the store.",
    },
}


def ensure_default_newsletter_topic() -> SubscriptionTopic | None:
    """Create the current schema's default newsletter topic if it has none.

    Returns the new topic, or ``None`` when nothing was created: the
    store already has a default newsletter topic, or a topic already
    uses the slug. A topic the merchant made is never touched, so this
    is safe to run on an existing store as well as a new one.

    The topic requires confirmation: a marketing list is double opt-in.
    """
    if SubscriptionTopic.objects.default_newsletter().exists():
        return None
    if SubscriptionTopic.objects.filter(
        slug=DEFAULT_NEWSLETTER_TOPIC_SLUG
    ).exists():
        return None

    with transaction.atomic():
        topic = SubscriptionTopic(
            slug=DEFAULT_NEWSLETTER_TOPIC_SLUG,
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
            is_default=True,
            requires_confirmation=True,
        )
        for language, fields in DEFAULT_NEWSLETTER_TOPIC_TRANSLATIONS.items():
            topic.set_current_language(language)
            topic.name = fields["name"]
            topic.description = fields["description"]
        topic.save()
    return topic
