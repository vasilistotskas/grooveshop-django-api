from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from datetime import timedelta

from extra_settings.models import Setting
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from core.utils.i18n import get_user_language

from user.models.subscription import SubscriptionTopic, UserSubscription

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from user.models.account import UserAccount as User
else:
    User = get_user_model()


def send_subscription_confirmation(
    subscription: UserSubscription, user: User
) -> bool:
    if check_subscription_before_send(
        user=user, topic_slug=subscription.topic.slug
    ):
        logger.warning(
            f"Attempted to send confirmation for already active subscription {subscription.id}"
        )
        return False

    if subscription.status != UserSubscription.SubscriptionStatus.PENDING:
        logger.warning(
            f"Attempted to send confirmation for non-pending subscription {subscription.id}"
        )
        return False

    if not subscription.confirmation_token:
        logger.error(
            f"No confirmation token for subscription {subscription.id}"
        )
        return False

    try:
        confirmation_url = Setting.get("SUBSCRIPTION_CONFIRMATION_URL")
        user = subscription.user
        language = get_user_language(user)

        context = {
            "user": user,
            "topic": subscription.topic,
            "subscription": subscription,
            "confirmation_url": confirmation_url.format(
                token=subscription.confirmation_token
            ),
            "SITE_NAME": settings.SITE_NAME,
            "SITE_URL": settings.NUXT_BASE_URL,
            "INFO_EMAIL": settings.INFO_EMAIL,
            "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            "LANGUAGE_CODE": language,
        }

        with translation.override(language):
            subject = _("Confirm your subscription to {topic}").format(
                topic=subscription.topic.name
            )
            html_message = render_to_string(
                "emails/subscription/confirmation.html", context
            )
            text_message = render_to_string(
                "emails/subscription/confirmation.txt", context
            )

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
            reply_to=[settings.INFO_EMAIL],
        )
        email.attach_alternative(html_message, "text/html")
        email.send()

        logger.info(
            f"Sent confirmation email for subscription {subscription.id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to send confirmation email for subscription {subscription.id}: {e}"
        )
        return False


def check_subscription_before_send(user: "User", topic_slug: str) -> bool:
    return UserSubscription.objects.filter(
        user=user,
        topic__slug=topic_slug,
        status=UserSubscription.SubscriptionStatus.ACTIVE,
    ).exists()


def generate_unsubscribe_link(user: "User", topic: SubscriptionTopic) -> str:
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    base_url = settings.API_BASE_URL.rstrip("/")
    unsubscribe_url = (
        f"{base_url}/api/v1/user/unsubscribe/{uid}/{token}/{topic.slug}"
    )

    return unsubscribe_url


def generate_blanket_unsubscribe_link(
    user: "AbstractBaseUser",
) -> str:
    """Build the blanket (no-topic) unsubscribe URL for ``user``.

    Mirrors ``generate_unsubscribe_link`` but emits the URL form that
    ``UnsubscribeAllView`` accepts — drops every active subscription
    when the recipient hits one-click unsubscribe in Gmail/Outlook.

    Use this in marketing/notification emails that aren't bound to a
    single topic (re-engagement, product alerts) so the email still
    carries an RFC 8058-compliant ``List-Unsubscribe`` header.

    Accepts ``AbstractBaseUser`` so callers with a generic FK
    (e.g. ``ProductAlert.user``) can pass it directly — only ``pk`` and
    ``default_token_generator`` compatibility are required.
    """
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))

    base_url = settings.API_BASE_URL.rstrip("/")
    return f"{base_url}/api/v1/user/unsubscribe/{uid}/{token}"


def build_list_unsubscribe_headers(
    unsubscribe_url: str, *, list_id: str
) -> dict[str, str]:
    """Build the ``List-Unsubscribe`` + ``List-ID`` header dict.

    Centralised so every marketing/notification email emits the same
    Gmail/Yahoo-friendly shape (per RFC 8058 + 2024 sender rules):

    * ``List-Unsubscribe`` — both ``mailto:`` and HTTPS so MUAs that
      don't support one-click POST still have a working link.
    * ``List-Unsubscribe-Post`` — flags one-click as supported.
    * ``List-ID`` — opaque list identifier; helps mailbox providers
      bucket per-list deliverability stats.
    """
    return {
        "List-Unsubscribe": (
            f"<mailto:{settings.INFO_EMAIL}?subject=unsubscribe>, "
            f"<{unsubscribe_url}>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-ID": f"{list_id}.{settings.SITE_NAME}",
    }


def send_newsletter(
    topic: SubscriptionTopic,
    subject: str,
    template_base: str,
    context: dict[str, Any],
    batch_size: int = 100,
    force: bool = False,
    dedup_window_hours: int = 24,
) -> dict[str, int]:
    """Send a newsletter to every ACTIVE subscriber of `topic`.

    The HTML/TXT body is rendered per-user under `translation.override(user.language_code)`;
    subject is caller-resolved (translate it in the caller if needed).

    Dedup: a Redis key `newsletter:sent:{slug}:{uid}:{date}` holds a flag for
    `dedup_window_hours`; set `force=True` to bypass (e.g. intentional re-send
    after a content fix).
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    active_subscriptions = UserSubscription.objects.filter(
        topic=topic, status=UserSubscription.SubscriptionStatus.ACTIVE
    ).select_related("user")

    today = timezone.now().date().isoformat()
    dedup_ttl_seconds = int(timedelta(hours=dedup_window_hours).total_seconds())

    for subscription in active_subscriptions.iterator(chunk_size=batch_size):
        user = subscription.user

        if not user.is_active or not user.email:
            stats["skipped"] += 1
            continue

        cache_key = f"newsletter:sent:{topic.slug}:{user.pk}:{today}"
        if not force and cache.get(cache_key):
            stats["skipped"] += 1
            continue

        unsubscribe_url = generate_unsubscribe_link(user, topic)
        user_context = context.copy()
        user_context.update(
            {
                "user": user,
                "topic": topic,
                "subscription": subscription,
                "unsubscribe_url": unsubscribe_url,
                "preferences_url": f"{settings.NUXT_BASE_URL}/account/subscriptions/",
                "SITE_NAME": settings.SITE_NAME,
                "SITE_URL": settings.NUXT_BASE_URL,
                "INFO_EMAIL": settings.INFO_EMAIL,
                "STATIC_BASE_URL": settings.STATIC_BASE_URL,
            }
        )

        try:
            with translation.override(get_user_language(user)):
                html_message = render_to_string(
                    f"{template_base}.html", user_context
                )
                text_message = render_to_string(
                    f"{template_base}.txt", user_context
                )

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
                reply_to=[settings.INFO_EMAIL],
                headers=build_list_unsubscribe_headers(
                    unsubscribe_url, list_id=topic.slug
                ),
            )
            email.attach_alternative(html_message, "text/html")
            email.send()

            cache.set(cache_key, True, timeout=dedup_ttl_seconds)
            stats["sent"] += 1

        except Exception as e:
            logger.error(f"Failed to send newsletter to {user.email}: {e}")
            stats["failed"] += 1

    logger.info(
        f"Newsletter sent for topic {topic.slug}: "
        f"{stats['sent']} sent, {stats['failed']} failed, {stats['skipped']} skipped"
    )

    return stats


def get_user_subscription_summary(user: "User") -> dict[str, Any]:
    subscriptions = UserSubscription.objects.filter(user=user).select_related(
        "topic"
    )

    summary = {
        "total": subscriptions.count(),
        "active": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).count(),
        "pending": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.PENDING
        ).count(),
        "unsubscribed": subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED
        ).count(),
        "by_category": {},
    }

    for subscription in subscriptions:
        category = subscription.topic.category
        if category not in summary["by_category"]:
            summary["by_category"][category] = {
                "total": 0,
                "active": 0,
            }

        summary["by_category"][category]["total"] += 1
        if subscription.status == UserSubscription.SubscriptionStatus.ACTIVE:
            summary["by_category"][category]["active"] += 1

    return summary
