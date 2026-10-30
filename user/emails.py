"""The account emails: their types, builders and samples.

The data-export link, the newsletter's double opt-in and the re-engagement
mail. ``emails.EmailsConfig`` imports this module at boot; the tasks
(``user.tasks``, ``core.tasks``) choose WHEN to send and keep the
bookkeeping after a send.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from core.utils.i18n import get_user_language
from core.utils.tenant_urls import get_tenant_frontend_url
from emails.declare import customer_email, marketing_email
from emails.gates import FeatureGate, ScheduleGate
from emails.recipients import Recipient, customer_of
from emails.types import RenderEnv
from user.utils.subscription import (
    generate_blanket_unsubscribe_link,
    subscription_confirmation_url,
)

if TYPE_CHECKING:
    from user.models import UserAccount, UserDataExport, UserSubscription

TEMPLATE_DIR = "emails/user"

#: The beat entry that sends the re-engagement mail. It is disabled on
#: production, which is what the gate reports as "not scheduled".
REENGAGEMENT_BEAT_ENTRY = "send-inactive-user-notifications"

SAMPLE_USER_ID = 12345


def _data_export(export: UserDataExport, env: RenderEnv) -> dict[str, Any]:
    return {
        "user": export.user,
        "download_url": get_tenant_frontend_url(
            f"/account/settings/privacy?export={export.token}",
            language=env.language,
        ),
        "expires_at": export.expires_at,
        "file_size_kb": round(export.file_size / 1024, 1),
    }


def _confirmation(
    subscription: UserSubscription, env: RenderEnv
) -> dict[str, Any]:
    # parler fixes a model's language when it is loaded: state it, so the
    # topic's name and description are the recipient's.
    topic = subscription.topic
    topic.set_current_language(env.language)
    return {
        "user": subscription.user,
        "topic": topic,
        "topic_name": topic.safe_translation_getter("name", any_language=True),
        "subscription": subscription,
        "confirmation_url": subscription_confirmation_url(
            subscription.confirmation_token, language=env.language
        ),
    }


def _reengagement(user: UserAccount, env: RenderEnv) -> dict[str, Any]:
    return {
        "user": {
            "id": user.id,
            "first_name": user.first_name,
            "username": user.username,
            "email": user.email,
        },
        "unsubscribe_url": generate_blanket_unsubscribe_link(user),
    }


def _subscriber(subscription: UserSubscription) -> list[Recipient]:
    """The subscription's address, in the language it was made in; a guest
    row has no account, so the store's language when it recorded none."""
    return [
        Recipient(
            address=subscription.recipient_email,
            language=subscription.language
            or get_user_language(subscription.user),
        )
    ]


def _sample_user(env: RenderEnv) -> UserAccount:
    from user.models import UserAccount

    return UserAccount(
        id=SAMPLE_USER_ID,
        email=env.recipient.address,
        username="maria",
        first_name="Maria",
        last_name="Papadopoulou",
        language_code=env.language,
    )


def _sample_data_export(env: RenderEnv) -> UserDataExport:
    from user.models import UserDataExport

    return UserDataExport(
        user=_sample_user(env),
        token="sample-token",
        file_size=2048,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _sample_subscription(env: RenderEnv) -> UserSubscription:
    from user.models import SubscriptionTopic, UserSubscription

    topic = SubscriptionTopic(
        slug="weekly-news", category=SubscriptionTopic.TopicCategory.MARKETING
    )
    for code, _label in settings.LANGUAGES:
        topic.set_current_language(code)
        topic.name = "Weekly news"
        topic.description = "Our weekly digest."
    topic.set_current_language(env.language)
    return UserSubscription(
        user=_sample_user(env),
        topic=topic,
        language=env.language,
        confirmation_token="sample-token",
        subscribed_at=timezone.now(),
    )


customer_email(
    "user.data_export_ready",
    title=_("Data export ready"),
    description=_(
        "Sent when the archive of a customer's personal data is ready to "
        "download."
    ),
    area="account",
    templates=f"{TEMPLATE_DIR}/data_export_ready",
    subject=gettext_noop("Your data export is ready"),
    build_context=_data_export,
    sample=_sample_data_export,
    recipients=lambda export: [customer_of(export.user)],
)
customer_email(
    "newsletter.confirmation",
    title=_("Newsletter confirmation"),
    description=_(
        "Sent to confirm a subscription before it starts (double opt-in)."
    ),
    area="account",
    templates="emails/subscription/confirmation",
    subject=gettext_noop("Confirm your subscription to {topic_name}"),
    build_context=_confirmation,
    sample=_sample_subscription,
    recipients=_subscriber,
    gates=(FeatureGate(feature="newsletter", label=_("Newsletter feature")),),
)
marketing_email(
    "user.reengagement",
    title=_("We miss you"),
    description=_(
        "Sent to a customer who has not signed in for a long while, a few "
        "times at most."
    ),
    area="account",
    templates=f"{TEMPLATE_DIR}/inactive_user_email_template",
    subject=gettext_noop("We miss you!"),
    build_context=_reengagement,
    sample=_sample_user,
    recipients=lambda user: [customer_of(user)],
    list_id="reengagement",
    gates=(
        ScheduleGate(
            entry=REENGAGEMENT_BEAT_ENTRY,
            label=_("Scheduled re-engagement run"),
        ),
    ),
)
