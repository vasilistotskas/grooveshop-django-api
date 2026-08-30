from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.internal.httpkit import clean_client_ip
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.utils import translation
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme

from core.utils.email_context import build_email_context
from core.utils.i18n import resolve_request_language
from core.utils.tenant_urls import get_tenant_frontend_url
from tenant.credentials import tenant_from_email, tenant_site_name

if TYPE_CHECKING:  # pragma: no cover
    from allauth.socialaccount.models import SocialAccount

logger = logging.getLogger(__name__)


class UserAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        user.language_code = resolve_request_language(request)
        if commit:
            user.save()
        return user

    def send_mail(self, template_prefix, email, context):
        """Merge in the shared branding/theme context before allauth
        renders ``account/email/<name>_message.{html,txt}``.

        Allauth builds its own context (``user``, ``code``/``key``,
        ``activate_url``, etc.) with no knowledge of
        ``core.utils.email_context.build_email_context`` — every other
        transactional email routes through it, but allauth's own
        ``send_mail`` never did, so ``core/templates/account/email/*``
        would render with unresolved ``SITE_NAME``/``THEME`` variables.
        allauth's own keys always win on collision (none exist today).
        """
        user = context.get("user") if isinstance(context, dict) else None
        language = (
            getattr(user, "language_code", None) if user else None
        ) or settings.LANGUAGE_CODE
        merged_context = {
            **build_email_context(LANGUAGE_CODE=language),
            **context,
        }
        with translation.override(language):
            return super().send_mail(template_prefix, email, merged_context)

    def format_email_subject(self, subject: str) -> str:
        """Prefix the subject with the active tenant's display name.

        ``ACCOUNT_EMAIL_SUBJECT_PREFIX`` is a settings-baked string
        evaluated once at process start (``f"[{SITE_NAME}] "``), so it
        can't vary per tenant. This override reads ``tenant_site_name()``
        at send time instead, so tenant-B users see "[Tenant B] ..."
        rather than the platform's baked-in ``SITE_NAME``. webside.gr
        (no active tenant row / empty ``Tenant.name``) falls back to
        ``settings.SITE_NAME`` inside ``tenant_site_name()``, so this
        stays byte-identical to the old ``ACCOUNT_EMAIL_SUBJECT_PREFIX``
        for the platform tenant.
        """
        prefix = f"[{tenant_site_name()}] "
        return prefix + force_str(subject)

    def get_from_email(self) -> str:
        """Return the deliverability-safe outbound sender for the
        active tenant: ``"{store name}" <DEFAULT_FROM_EMAIL>`` — see
        ``tenant.credentials.tenant_from_email`` for the DMARC
        rationale.
        """
        return tenant_from_email()

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

    # ``pre_social_login`` is intentionally NOT overridden.
    #
    # Earlier versions issued an ``ImmediateHttpResponse(HttpResponseRedirect)``
    # to a frontend URL when the matched local account had TOTP/WebAuthn
    # enrolled.  That short-circuited allauth's stage pipeline, which means
    # the headless contract was never produced and the Nuxt callback page
    # had no actionable state to resume.
    #
    # The headless ``AuthenticateStage`` (``allauth.mfa.stages``) already
    # intercepts *every* completed login — password and social — and emits a
    # 401 ``{"flows": [{"id": "mfa_authenticate", "is_pending": true}]}``
    # envelope that the Nuxt ``useAuth`` composable + ``handleAllAuthError``
    # know how to route to the TOTP challenge screen.  Verified against the
    # storefront's ``app/utils/error.ts`` flow handler and
    # ``shared/constants/index.ts`` ``MFA_AUTHENTICATE`` mapping.
    #
    # Removing the override therefore restores the canonical headless
    # behaviour without weakening MFA enforcement.

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
        return get_tenant_frontend_url("/account")
