from __future__ import annotations

import logging

from allauth.account.signals import password_changed, user_signed_up
from django.dispatch import receiver

from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils.crypto import get_random_string

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import send_subscription_confirmation

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialLogin

User = get_user_model()


logger = logging.getLogger(__name__)


@receiver(
    password_changed, dispatch_uid="user.revoke_knox_tokens_on_password_change"
)
def revoke_knox_tokens_on_password_change(request, user, **kwargs):
    """
    Revoke all Knox access tokens when the user changes their password.
    ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True handles allauth sessions;
    this ensures Knox tokens (used for REST API + WebSocket) are also
    invalidated so any active API clients lose access immediately.
    """
    from knox.models import get_token_model  # noqa: PLC0415

    revoked = get_token_model().objects.filter(user=user).delete()
    logger.info(
        "Revoked Knox tokens after password change",
        extra={"user_id": user.pk, "revoked_count": revoked[0]},
    )


@receiver(user_signed_up, dispatch_uid="user.populate_profile")
def populate_profile(
    sociallogin: SocialLogin | None = None, user=None, **kwargs
):
    """Download the provider avatar for new social account signups."""
    if not sociallogin or not user:
        # Email/password signups have no sociallogin — nothing to do.
        return

    provider = sociallogin.account.provider
    picture_url = None

    match provider:
        case "facebook":
            picture_url = f"http://graph.facebook.com/{sociallogin.account.uid}/picture?type=large"
        case "google":
            picture_url = sociallogin.account.extra_data.get("picture")
        case "discord":
            avatar_id = sociallogin.account.extra_data.get("id")
            avatar_hash = sociallogin.account.extra_data.get("avatar")
            if avatar_id and avatar_hash:
                picture_url = f"https://cdn.discordapp.com/avatars/{avatar_id}/{avatar_hash}.png"
            else:
                logger.warning(
                    "Missing avatar_id or avatar_hash for Discord provider"
                )
        case "github":
            picture_url = sociallogin.account.extra_data.get("avatar_url")
        case _:
            logger.warning("Unsupported social provider: %s", provider)

    if picture_url:
        # Dispatch to Celery task to avoid blocking the HTTP response
        from user.tasks import download_social_avatar_task

        download_social_avatar_task.delay(
            user_id=user.pk, picture_url=picture_url
        )


@receiver(
    post_save, sender=User, dispatch_uid="user.create_default_subscriptions"
)
def create_default_subscriptions(sender, instance, created, **kwargs):
    if created:
        default_topics = SubscriptionTopic.objects.filter(
            is_active=True, is_default=True
        )

        for topic in default_topics:
            subscription = UserSubscription.objects.create(
                user=instance,
                topic=topic,
                status=(
                    UserSubscription.SubscriptionStatus.PENDING
                    if topic.requires_confirmation
                    else UserSubscription.SubscriptionStatus.ACTIVE
                ),
            )

            if topic.requires_confirmation:
                subscription.confirmation_token = get_random_string(64)
                subscription.save()
                send_subscription_confirmation(subscription, instance)
