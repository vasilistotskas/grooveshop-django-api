from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.core.internal.httpkit import clean_client_ip
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme

from core.utils.i18n import resolve_request_language

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount, SocialLogin

logger = logging.getLogger(__name__)


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

    def get_client_ip(self, request: HttpRequest) -> str:
        """
        Resolve the client IP for UserSession tracking and rate limiting.

        Prefers X-Real-IP (set by the Nuxt proxy via h3's getRequestIP, which
        resolves the real client IP from the X-Forwarded-For chain) and falls
        back to REMOTE_ADDR so direct-to-Django callers (health probes,
        Celery-triggered HTTP, integration tests) don't trip allauth's strict
        "header-or-nothing" default introduced in 65.14.2.
        """
        ip = request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR")
        cleaned = clean_client_ip(ip) if ip else None
        if not cleaned:
            raise PermissionDenied("Unable to determine client IP address")
        return cleaned


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    # Email-based auto-connect is handled by allauth via:
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
    #   SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

    def pre_social_login(
        self, request: HttpRequest, sociallogin: "SocialLogin"
    ) -> None:
        """
        Block auto-authentication when the matched local account has MFA
        enrolled.

        After allauth resolves the social identity to a local user, it
        would normally complete the login transparently.  If that user has
        TOTP / WebAuthn active we must NOT bypass the second factor, so we
        redirect to the MFA verification page instead of allowing the
        social provider to act as a sufficient credential on its own.

        The redirect only fires when ``sociallogin.is_existing`` is True
        (i.e. allauth has already found the matching local account); new
        sign-ups skip this path and proceed normally.
        """
        from allauth.mfa.models import Authenticator  # noqa: PLC0415

        user = sociallogin.user
        if not sociallogin.is_existing or user is None:
            # New signup — no existing account to protect.
            return

        has_mfa = (
            Authenticator.objects.filter(
                user_id=user.pk,
            )
            .exclude(
                type=Authenticator.Type.RECOVERY_CODES,
            )
            .exists()
        )

        if not has_mfa:
            return

        logger.info(
            "Social login blocked: MFA enrolled on matched account",
            extra={
                "user_id": user.pk,
                "provider": sociallogin.account.provider,
            },
        )
        # TODO(maintainers): This redirect is incorrect for headless allauth.
        #
        # Problem: ``ImmediateHttpResponse(HttpResponseRedirect(...))`` issues
        # a browser redirect to ``/account/security/2fa``.  In headless mode
        # allauth has no pending-login state at that URL — the Nuxt page
        # receives the redirect but has no allauth session it can resume, so
        # the user sees the security page without any actionable prompt.
        #
        # Correct headless pattern: remove this entire ``pre_social_login``
        # override and rely on allauth's ``AuthenticateStage``
        # (``allauth.mfa.stages``).  The stage pipeline is triggered
        # automatically after social login completes and its
        # ``_should_handle`` already calls
        # ``is_mfa_enabled(user, [TOTP, WEBAUTHN])``, emitting a 401
        # ``{"flows": [{"id": "mfa_authenticate", "is_pending": true}]}``
        # JSON envelope that the Nuxt ``useAuth`` composable handles.
        #
        # The ``has_mfa`` guard and this block are therefore entirely
        # redundant once the stage pipeline is trusted.  The correct fix
        # is to delete this method, but it requires verifying that:
        #   1. The Nuxt social-login callback path (``/account/login/callback``)
        #      inspects the 401 pending-flows response.
        #   2. The TOTP challenge screen is reachable from that callback before
        #      the session expires.
        # Until that Nuxt-side verification is done, keep the redirect as a
        # hard block (MFA user cannot complete social login at all, which is
        # better than silently bypassing the second factor).
        #
        # Audit ref: cleanup-2026-05-02 / Fix 2.
        mfa_url = f"{settings.NUXT_BASE_URL}/account/security/2fa"
        raise ImmediateHttpResponse(HttpResponseRedirect(mfa_url))

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        if (
            not user.language_code
            or user.language_code == settings.LANGUAGE_CODE
        ):
            user.language_code = resolve_request_language(request)
            user.save(update_fields=["language_code"])
        return user

    def get_connect_redirect_url(self, request, socialaccount: SocialAccount):
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
