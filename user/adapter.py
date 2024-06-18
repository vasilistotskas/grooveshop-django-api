from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialLogin  # isort:skip
    from allauth.socialaccount.models import SocialAccount  # isort:skip

User = get_user_model()


class UserAccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin: SocialLogin):
        email = None

        # Ignore existing social accounts, just do this stuff for new ones.
        if sociallogin.is_existing:
            return

        # some social logins don't have an email address, e.g. facebook accounts
        # with mobile numbers only, but allauth takes care of this case so just
        # ignore it.
        # we are looking for email in two places here:
        if "email" in sociallogin.account.extra_data:
            email = sociallogin.account.extra_data["email"].lower()

        for email_address in sociallogin.email_addresses:
            if email_address.verified:
                email = email_address.email
                break

        if not email:
            return

        # check if given email address already exists.
        user = User.objects.filter(email__iexact=email).first()
        if user:
            # if it does, connect this new social login to the existing user.
            sociallogin.connect(request, user)

    def get_connect_redirect_url(self, request: HttpRequest, social_account: SocialAccount) -> str:
        url = request.POST.get("next") or request.GET.get("next")
        return url if url else f"{settings.NUXT_BASE_URL}/account"
