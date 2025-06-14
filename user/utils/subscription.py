import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from user.models.subscription import SubscriptionTopic, UserSubscription

logger = logging.getLogger(__name__)
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
        context = {
            "user": subscription.user,
            "topic": subscription.topic,
            "confirmation_url": settings.SUBSCRIPTION_CONFIRMATION_URL.format(
                token=subscription.confirmation_token
            ),
            "site_name": getattr(settings, "SITE_NAME", "Our Site"),
            "support_email": getattr(
                settings, "SUPPORT_EMAIL", "support@example.com"
            ),
        }

        subject = _("Confirm your subscription to {topic}").format(
            topic=subscription.topic.name
        )

        html_message = render_to_string(
            "subscription/emails/confirmation.html", context
        )

        text_message = strip_tags(html_message)

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[subscription.user.email],
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

    base_url = getattr(settings, "SITE_URL", "https://example.com")
    unsubscribe_url = f"{base_url}/unsubscribe/{uid}/{token}/{topic.slug}/"

    return unsubscribe_url


def send_newsletter(
    topic: SubscriptionTopic,
    subject: str,
    template_name: str,
    context: dict[str, Any],
    batch_size: int = 100,
) -> dict[str, int]:
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    active_subscriptions = UserSubscription.objects.filter(
        topic=topic, status=UserSubscription.SubscriptionStatus.ACTIVE
    ).select_related("user")

    for subscription in active_subscriptions.iterator(chunk_size=batch_size):
        user = subscription.user

        if not user.is_active:
            stats["skipped"] += 1
            continue

        user_context = context.copy()
        user_context.update(
            {
                "user": user,
                "topic": topic,
                "subscription": subscription,
                "unsubscribe_url": generate_unsubscribe_link(user, topic),
                "preferences_url": f"{getattr(settings, 'SITE_URL', '')}/account/subscriptions/",
            }
        )

        try:
            html_message = render_to_string(template_name, user_context)
            text_message = strip_tags(html_message)

            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email.attach_alternative(html_message, "text/html")
            email.send()

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
