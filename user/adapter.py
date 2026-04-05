from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

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
        allowed_hosts = {
            settings.APP_MAIN_HOST_NAME,
            settings.NUXT_BASE_DOMAIN,
        }
        if url and url_has_allowed_host_and_scheme(
            url, allowed_hosts=allowed_hosts
        ):
            return url
        return f"{settings.NUXT_BASE_URL}/account"
