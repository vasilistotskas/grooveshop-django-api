from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.internal.httpkit import clean_client_ip
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme

from core.client_ip import client_ip_or_peer
from core.utils.i18n import get_user_language, resolve_request_language
from core.utils.tenant_urls import get_tenant_frontend_url
from tenant.membership import bound_tenant
from tenant.middleware import tenant_domain_set

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
        """Send allauth's mail as the email type its prefix names.

        allauth builds the mail's context and calls this with the
        template prefix (``account/email/login_code``); the prefix picks
        the type (``user.emails.ALLAUTH_TYPES``, a prefix it does not
        know raises ``KeyError``) and the pipeline renders, brands and
        sends it like every other mail.

        The language is the recipient's when allauth names one, and
        otherwise the visitor's who asked for the email (an unknown
        address, an address that already has an account, a notice).
        ``self.request`` is that visitor's request: ``BaseAdapter``
        reads it from ``allauth.core.context.request``, which
        ``AccountMiddleware`` sets for the whole request.

        The mail is sent from a task, not here. allauth's own
        ``send_mail`` talks SMTP inside the view, so on 2026-09-15 a
        transient smtp.gmail.com 421 ("Server busy") surfaced as a 500
        from ``/_allauth/app/v1/auth/signup`` and a real person could not
        create an account. In a task a 421 is a retry. The context is
        reduced to text for the queue (``AllauthMail``), and the task is
        queued after the commit: the confirmation must not go out for a
        signup whose transaction then rolls back, and the schema has to
        be pinned at registration rather than read from a connection
        that has since snapped back to public.
        """
        from tenant.celery import dispatch_on_commit
        from user.emails import ALLAUTH_TYPES, AllauthMail
        from user.tasks import send_allauth_email_task

        type_key = ALLAUTH_TYPES[template_prefix]
        user = context.get("user")
        language = (
            get_user_language(user)
            if user is not None
            else resolve_request_language(self.request)
        )
        mail = AllauthMail.from_allauth(
            address=email, language=language, context=context
        )
        dispatch_on_commit(
            send_allauth_email_task, kwargs=mail.task_kwargs(type_key)
        )

    def get_client_ip(self, request: HttpRequest) -> str:
        """
        Resolve the client IP for UserSession tracking and rate limiting.

        ``core.client_ip.client_ip_or_peer``: the edge-proven visitor, else
        the peer Traefik saw, else REMOTE_ADDR — so direct-to-Django callers
        (health probes, Celery-triggered HTTP, integration tests) still
        don't trip allauth's strict "header-or-nothing" default introduced
        in 65.14.2. A bare X-Real-IP is not believed: on the SSR path the
        Nuxt proxy fills it from the caller's own CF-Connecting-IP, which
        allauth's login-failure limits would then key on.
        """
        ip = client_ip_or_peer(request)
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
        """Honour ``next`` only when it stays on the ACTIVE store's own
        hostnames (its ``TenantDomain`` rows); the platform host is the
        allowed set only outside a store's schema. A static platform
        allowlist would both reject every other store's legitimate
        ``next`` and accept a redirect onto the first store's domain.
        """
        url = request.POST.get("next") or request.GET.get("next")
        allowed_hosts = tenant_domain_set(bound_tenant()) or {
            settings.APP_MAIN_HOST_NAME
        }
        if url and url_has_allowed_host_and_scheme(
            url, allowed_hosts=allowed_hosts
        ):
            return url
        return get_tenant_frontend_url(
            "/account", language=resolve_request_language(request)
        )
