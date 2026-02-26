from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount


class UserAccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    # Email-based auto-connect is handled by allauth via:
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

    def get_connect_redirect_url(self, request, social_account: SocialAccount):
        url = request.POST.get("next") or request.GET.get("next")
        return url if url else f"{settings.NUXT_BASE_URL}/account"
