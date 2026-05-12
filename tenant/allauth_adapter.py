from __future__ import annotations

import logging

from django import forms
from django.db import connection
from django.utils.translation import gettext_lazy as _

from tenant.membership import user_has_tenant_access
from user.adapter import SocialAccountAdapter, UserAccountAdapter

logger = logging.getLogger(__name__)


def _resolve_tenant_from_request(request):
    """Return the Tenant for ``request.get_host()`` or ``None``.

    The login gate in ``pre_login`` previously read
    ``connection.tenant`` from a thread-local. Under Daphne/Channels
    ``database_sync_to_async`` reuses threads across requests — a
    pooled worker thread can hold a stale ``connection.tenant`` from
    an earlier request and grant access to the wrong tenant (H7 in
    MULTI_TENANT_AUDIT.md). Resolving from the request host bypasses
    the thread-local entirely so the gate is correct regardless of
    pool state.
    """
    if request is None:
        return None
    try:
        host = request.get_host()
    except Exception:
        return None
    # Strip the port so ``store.com:443`` matches a row with
    # ``domain='store.com'``.
    host = host.split(":", 1)[0]
    if not host:
        return None
    from tenant.models import TenantDomain  # noqa: PLC0415

    domain = (
        TenantDomain.objects.select_related("tenant")
        .filter(domain__iexact=host)
        .first()
    )
    return getattr(domain, "tenant", None) if domain else None


def _ensure_member_membership(user) -> None:
    """Grant a MEMBER membership in the active tenant, if any.

    Called from both the email signup adapter and the social signup
    adapter so either path produces a usable login. No-op when the
    request is in the public schema (admin, platform routines).
    """
    tenant = getattr(connection, "tenant", None)
    if tenant is None or getattr(tenant, "schema_name", "public") == "public":
        return
    from tenant.models import TenantMembershipRole, UserTenantMembership

    UserTenantMembership.objects.get_or_create(
        user=user,
        tenant=tenant,
        defaults={
            "role": TenantMembershipRole.MEMBER,
            "is_active": True,
        },
    )


class TenantAccountAdapter(UserAccountAdapter):
    """Dynamic frontend URLs + per-tenant login gating.

    Two responsibilities:

    1. Email links (confirmation, password reset) use the tenant's
       primary domain so a tenant-B user never clicks a link that takes
       them to webside.gr.

    2. Login is refused if the authenticated user has no active
       ``UserTenantMembership`` for the current tenant. Without this
       gate, a user registered on tenant A could log into tenant B's
       storefront (same global ``UserAccount``, different domain) and
       read tenant B's data.
    """

    def _get_tenant_domain(self):
        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            return None
        domain = tenant.domains.filter(is_primary=True).first()
        return domain.domain if domain else None

    def _scheme(self) -> str:
        """Return the right URL scheme for this deployment.

        Defaults to ``https`` for production and anything else that
        doesn't override. Falls back to ``ACCOUNT_DEFAULT_HTTP_PROTOCOL``
        so dev environments running on plain HTTP don't email users a
        link they cannot open.
        """
        from django.conf import settings

        return getattr(settings, "ACCOUNT_DEFAULT_HTTP_PROTOCOL", "https")

    def get_email_confirmation_url(self, request, emailconfirmation):
        # Accept either a full HMAC emailconfirmation model or the raw
        # key string depending on caller; allauth's headless stack
        # sometimes passes the model and sometimes the key.
        key = getattr(emailconfirmation, "key", None) or str(emailconfirmation)
        domain = self._get_tenant_domain()
        if domain:
            return f"{self._scheme()}://{domain}/account/verify-email/{key}"
        return super().get_email_confirmation_url(request, emailconfirmation)

    def get_reset_password_url(self, request):
        domain = self._get_tenant_domain()
        if domain:
            return f"{self._scheme()}://{domain}/account/password/reset"
        return super().get_reset_password_url(request)

    def pre_login(self, request, user, **kwargs):
        """Block login when the user has no membership in this tenant.

        Runs after credential validation but before the session cookie
        is issued / the Knox token is minted. Raising
        ``forms.ValidationError`` surfaces as a 400 with a localizable
        message the storefront can show the user ("You don't have
        access to this store — contact support or register here.").

        Resolves the tenant from the request host (NOT
        ``connection.tenant``) so a stale thread-local cannot let a
        user authenticated on tenant A pass the gate on tenant B's
        domain.
        """
        tenant = _resolve_tenant_from_request(request)
        # No tenant attached (public schema, admin login, health probes)
        # — nothing to gate on, fall through to the default.
        if (
            tenant is not None
            and getattr(tenant, "schema_name", "public") != "public"
        ):
            if not user_has_tenant_access(user, tenant):
                raise forms.ValidationError(
                    _("You do not have access to this store."),
                    code="no_tenant_membership",
                )
        return super().pre_login(request, user, **kwargs)

    def save_user(self, request, user, form, commit=True):
        """Create a MEMBER membership alongside the new user account.

        Signup requests arrive on a tenant domain, so the TenantMiddleware
        has already pointed ``connection`` at that tenant by the time
        this runs. Creating the membership here means a user can
        sign up and immediately log in on the same tenant without a
        separate admin step.
        """
        user = super().save_user(request, user, form, commit=commit)
        if commit:
            _ensure_member_membership(user)
        return user


class TenantSocialAccountAdapter(SocialAccountAdapter):
    """Social-login sibling of ``TenantAccountAdapter``.

    The email-signup adapter handles memberships for password signups,
    but a first-time Google / Facebook / GitHub login skips that path
    entirely and runs through the social adapter instead. Without this
    override, a new social user would log in, pass credentials, then
    hit ``pre_login`` (which runs for both flows) and get rejected with
    "You do not have access to this store" — even though the signup
    just succeeded on the same tenant.

    Additionally overrides ``get_app`` to look for a per-tenant
    ``SocialApp`` row (linked to the tenant's primary domain via the Sites
    framework) before falling back to the global ``SOCIALACCOUNT_PROVIDERS``
    settings config.  This enables tenants to use their own OAuth app
    credentials — e.g. so each tenant's OAuth consent screen shows their
    own brand name.

    Design rationale — Sites vs new FK:
    allauth's ``SocialApp`` already has a M2M to ``django.contrib.sites.Site``.
    Each tenant's primary domain corresponds to a ``Site`` row whose domain
    matches.  Using the existing Sites relationship avoids a new DB migration
    and keeps allauth's own tooling (admin, shell) usable for managing apps.
    """

    def get_app(self, request, provider, client_id=None):
        """Return the ``SocialApp`` for ``provider`` on the current tenant.

        Lookup order:
        1. ``SocialApp`` rows linked via Sites to the tenant's primary domain.
        2. Super (settings-based APP config or unfiltered DB lookup).

        Falls back gracefully when:
        - The Sites framework has no row for the tenant domain.
        - No per-tenant ``SocialApp`` is configured (single-tenant deployments).

        Resolves the tenant from ``request.get_host()`` rather than
        ``connection.tenant``. Under Daphne/Channels with
        ``database_sync_to_async`` thread pooling, ``connection.tenant``
        can be stale and would return a different tenant's OAuth app
        config (H7 in MULTI_TENANT_AUDIT.md — same fix pattern as
        ``pre_login``).
        """
        tenant = _resolve_tenant_from_request(request)
        if (
            tenant is not None
            and getattr(tenant, "schema_name", "public") != "public"
        ):
            try:
                from allauth.socialaccount.models import (  # noqa: PLC0415
                    SocialApp,
                )
                from django.contrib.sites.models import Site  # noqa: PLC0415

                # Find the Site row whose domain matches this tenant's
                # primary domain.  Uses select_related to avoid N+1.
                primary_domain_obj = tenant.domains.filter(
                    is_primary=True
                ).first()
                if primary_domain_obj:
                    site = Site.objects.filter(
                        domain=primary_domain_obj.domain
                    ).first()
                    if site:
                        qs = SocialApp.objects.filter(
                            provider=provider, sites=site
                        )
                        if client_id:
                            qs = qs.filter(client_id=client_id)
                        app = qs.first()
                        if app is not None:
                            return app
            except Exception:
                logger.warning(
                    "TenantSocialAccountAdapter.get_app: error during "
                    "per-tenant lookup for provider %r on tenant %r",
                    provider,
                    getattr(tenant, "schema_name", "?"),
                    exc_info=True,
                )

        return super().get_app(request, provider, client_id=client_id)

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        _ensure_member_membership(user)
        return user
