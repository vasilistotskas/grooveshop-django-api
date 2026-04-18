from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme

from core.utils.i18n import resolve_request_language

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount


class UserAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        user.language_code = resolve_request_language(request)
        if commit:
            user.save()
        return user

    def send_mail(self, template_prefix, email, context):
        user = context.get("user") if isinstance(context, dict) else None
        language = (
            getattr(user, "language_code", None) if user else None
        ) or settings.LANGUAGE_CODE
        with translation.override(language):
            return super().send_mail(template_prefix, email, context)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    # Email-based auto-connect is handled by allauth via:
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        if (
            not user.language_code
            or user.language_code == settings.LANGUAGE_CODE
        ):
            user.language_code = resolve_request_language(request)
            user.save(update_fields=["language_code"])
        return user

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
