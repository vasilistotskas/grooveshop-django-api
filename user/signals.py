from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.socialaccount.signals import (
    pre_social_login,
    social_account_added,
    social_account_removed,
    social_account_updated,
)
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.crypto import get_random_string

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import send_subscription_confirmation

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount, SocialLogin

User = get_user_model()


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
