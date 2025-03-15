from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.socialaccount.signals import (
    pre_social_login,
    social_account_added,
    social_account_removed,
    social_account_updated,
)
from django.dispatch import receiver

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount, SocialLogin


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
