from __future__ import annotations

import logging
from tempfile import NamedTemporaryFile

import requests
from allauth.account.signals import user_signed_up
from django.core.files import File
from django.dispatch import receiver

from typing import TYPE_CHECKING

from allauth.socialaccount.signals import (
    pre_social_login,
    social_account_added,
    social_account_removed,
    social_account_updated,
)
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils.crypto import get_random_string

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import send_subscription_confirmation

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount, SocialLogin

User = get_user_model()


logger = logging.getLogger(__name__)


@receiver(user_signed_up)
def populate_profile(sociallogin=None, user=None, **kwargs):
    if not sociallogin or not user:
        logger.warning("No sociallogin or user passed to populate_profile")
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
            logger.warning(f"Unsupported provider: {provider}")

    if picture_url:
        try:
            response = requests.get(picture_url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to retrieve image from {picture_url}: {e}")
            return

        with NamedTemporaryFile(delete=True) as img_temp:
            img_temp.write(response.content)
            img_temp.flush()
            image_filename = (
                f"image_{user.first_name}_{user.last_name}_{user.pk}.jpg"
            )
            user.image.save(image_filename, File(img_temp))


@receiver(pre_social_login)
def pre_social_login_signal(
    sender, request, sociallogin: SocialLogin, **kwargs
):
    pass


@receiver(social_account_added)
def social_account_added_signal(sender, **kwargs):
    pass


@receiver(social_account_updated)
def social_account_updated_signal(
    sender, request, sociallogin: SocialLogin, **kwargs
):
    pass


@receiver(social_account_removed)
def social_account_removed_signal(
    sender, request, socialaccount: SocialAccount, **kwargs
):
    pass


@receiver(post_save, sender=User)
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
