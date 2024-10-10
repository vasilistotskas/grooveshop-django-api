from __future__ import annotations

from typing import override
from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialLogin  # isort:skip
    from allauth.socialaccount.models import SocialAccount  # isort:skip

User = get_user_model()


class UserAccountAdapter(DefaultAccountAdapter):
    pass


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    @override
    def pre_social_login(self, request, sociallogin: SocialLogin):
        email = None

        if sociallogin.is_existing:
            return

        if "email" in sociallogin.account.extra_data:
            email = sociallogin.account.extra_data["email"].lower()

        for email_address in sociallogin.email_addresses:
            if email_address.verified:
                email = email_address.email
                break

        if not email:
            return

        user = User.objects.filter(email__iexact=email).first()
        if user:
            sociallogin.connect(request, user)

    @override
    def get_connect_redirect_url(self, request, social_account: SocialAccount) -> str:
        url = request.POST.get("next") or request.GET.get("next")
        return url if url else f"{settings.NUXT_BASE_URL}/account"
