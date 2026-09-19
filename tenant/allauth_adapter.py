from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from allauth.headless.adapter import DefaultHeadlessAdapter
from django.db import connection

from user.adapter import SocialAccountAdapter, UserAccountAdapter

logger = logging.getLogger(__name__)


def _default_url_scheme() -> str:
    """Return the right URL scheme for this deployment.

    Defaults to ``https`` for production and anything else that
    doesn't override. Falls back to ``ACCOUNT_DEFAULT_HTTP_PROTOCOL``
    so dev environments running on plain HTTP don't email users a
    link they cannot open.
    """
    from django.conf import settings

    return getattr(settings, "ACCOUNT_DEFAULT_HTTP_PROTOCOL", "https")


def _resolve_tenant_from_request(request):
    """Return the Tenant for ``request.get_host()`` or ``None``.

    Resolves from the request host rather than ``connection.tenant``.
    Under Daphne/Channels ``database_sync_to_async`` reuses threads
    across requests, so a pooled worker can hold a stale
    ``connection.tenant`` from an earlier request and hand back another
    tenant's OAuth app config. Reading the
    host bypasses the thread-local entirely.
    """
    if request is None:
        return None
    try:
        host = request.get_host()
    except Exception:
        return None
    # Strip the port and the trailing dot. ``domain__iexact`` handles
    # case insensitivity at the DB layer, but a fully-qualified host
    # like ``store.com.`` is semantically identical to ``store.com``
    # and would otherwise silently miss the lookup → fall through to
    # the public-schema branch, granting access to a different tenant.
    host = host.split(":", 1)[0].rstrip(".")
    if not host:
        return None
    from tenant.models import TenantDomain

    domain = (
        TenantDomain.objects.select_related("tenant")
        .filter(domain__iexact=host)
        .first()
    )
    return getattr(domain, "tenant", None) if domain else None


class TenantAccountAdapter(UserAccountAdapter):
    """Dynamic frontend URLs for tenant-scoped account emails.

    Email links (confirmation, password reset) use the tenant's primary
    domain so a tenant-B user never clicks a link that takes them to
    another store.

    Login needs no tenant gate: customers are per-schema, so a shopper
    registered at tenant A simply does not exist in tenant B's user
    table. See ``pre_login``.
    """

    def clean_password(self, password: str, user=None) -> str:
        """Refuse a new password for a shared demo login.

        THIS is the hook that produces a usable refusal. It runs during
        FORM VALIDATION — `ChangePasswordInput` and `ResetPasswordInput`
        both call it with the user — so allauth turns the error into a
        400 with a message on the field. `set_password` below is called
        after validation, where raising only produces a 500.

        A middleware cannot do this job: `/_allauth/app/v1/**` resolves
        its session token inside a VIEW DECORATOR, so `request.user` is
        still anonymous while middleware runs. Verified against staging
        on 2026-09-19 — the middleware never fired and the request fell
        through to `set_password`, which 500'd.
        """
        from django.core.exceptions import ValidationError

        from core.demo_account import is_demo_account

        if user is not None and is_demo_account(user):
            raise ValidationError(
                "This is a shared demo account, so its password cannot "
                "be changed."
            )
        return super().clean_password(password, user=user)

    def set_password(self, user, password: str) -> None:
        """Programmatic backstop for a password change on a demo login.

        Unreachable through the API: ``clean_password`` above rejects
        the input first, on both the change and the reset flow. This
        covers a path that skips form validation altogether — a future
        flow, a management command — where a 500 is the right outcome
        because nothing should be calling it.

        The seeder is unaffected: it calls ``user.set_password``
        directly, as does the nightly reset, so the account can still be
        restored to its published password.
        """
        from django.core.exceptions import ValidationError

        from core.demo_account import is_demo_account

        if is_demo_account(user):
            raise ValidationError(
                "This is a shared demo account, so its password cannot "
                "be changed."
            )
        super().set_password(user, password)

    def can_delete_email(self, email_address) -> bool:
        """Keep the demo account's published address on the account.

        Login is by email (``ACCOUNT_LOGIN_METHODS = {"email"}``), so
        deleting the address printed on the login card is the one email
        operation that costs everybody else their way in. Adding
        another one is already refused by the middleware; this closes
        the same door from the model side.
        """
        from core.demo_account import demo_account_emails

        email = str(getattr(email_address, "email", "") or "").strip().lower()
        if email and email in demo_account_emails():
            return False
        return super().can_delete_email(email_address)

    def _get_tenant_domain(self):
        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            return None
        domain = tenant.domains.filter(is_primary=True).first()
        return domain.domain if domain else None

    def _scheme(self) -> str:
        return _default_url_scheme()

    def get_email_confirmation_url(self, request, emailconfirmation):
        # Accept either a full HMAC emailconfirmation model or the raw
        # key string depending on caller; allauth's headless stack
        # sometimes passes the model and sometimes the key.
        #
        # This IS a genuinely-invoked hook: ``DefaultAccountAdapter.
        # send_confirmation_mail`` calls ``self.get_email_confirmation_url``
        # directly (verified against the installed allauth package),
        # unlike ``get_reset_password_url`` below (removed — allauth
        # never calls an adapter method by that name).
        key = getattr(emailconfirmation, "key", None) or str(emailconfirmation)
        domain = self._get_tenant_domain()
        if domain:
            return f"{self._scheme()}://{domain}/account/verify-email/{key}"
        return super().get_email_confirmation_url(request, emailconfirmation)

    def pre_login(self, request, user, **kwargs):
        """No tenant gate here — per-schema user tables ARE the gate.

        Customers live in their tenant's own schema: ``user`` is in both
        SHARED_APPS and TENANT_APPS, and with ``search_path =
        "<tenant>", public`` the tenant copy always wins. A shopper who
        registered at tenant A therefore has no row, no allauth records
        and no Knox token in tenant B, so credential lookup on B's host
        fails before any gate would run.

        This used to require a ``UserTenantMembership``, which is a
        PUBLIC-schema table whose FK targets ``public.user_useraccount``.
        A shopper created in a tenant schema has no public row, so the
        grant that accompanied signup raised ForeignKeyViolation — the
        signup 500'd with the account already written, and every later
        login 500'd here. Membership is now what its name says: a STAFF
        grant over a tenant, held by platform-public identities only
        (see ``admin.admin.MyAdminSite.has_permission``).
        """
        return super().pre_login(request, user, **kwargs)


class TenantSocialAccountAdapter(SocialAccountAdapter):
    """Social-login sibling of ``TenantAccountAdapter``.

    A first-time Google / Facebook / GitHub login creates the shopper in
    the tenant's own schema, which is what scopes them to this store —
    no membership grant is involved for either signup path.

    Overrides ``get_app`` to look for a per-tenant
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

    @staticmethod
    def _allowed_providers(request) -> set[str] | None:
        """The tenant's ``SOCIAL_LOGIN_PROVIDERS`` whitelist, or ``None``
        for "no restriction".

        Semantics of the json setting (validated by
        ``tenant.validators.validate_social_login_providers_setting``):
        ``["*"]`` (the default) = every configured provider; a list of
        provider ids = exactly those; ``[]`` = social login fully off.
        Resolved from the request host (H7 — never ``connection.tenant``)
        and read inside an explicit ``schema_context`` because
        ``Setting`` is a TENANT_APPS model.
        """
        tenant = _resolve_tenant_from_request(request)
        if (
            tenant is None
            or getattr(tenant, "schema_name", "public") == "public"
        ):
            return None
        try:
            from django_tenants.utils import schema_context
            from extra_settings.models import Setting

            with schema_context(tenant.schema_name):
                value = Setting.get("SOCIAL_LOGIN_PROVIDERS", default=None)
        except Exception:
            # Fail CLOSED: the whitelist is a security control. A DB blip
            # or a half-provisioned schema disables social login for this
            # request; it must never silently re-enable every configured
            # provider the merchant switched off.
            logger.exception(
                "SOCIAL_LOGIN_PROVIDERS lookup failed for tenant %r — "
                "social login disabled for this request",
                getattr(tenant, "schema_name", "?"),
            )
            return set()
        if not isinstance(value, list) or "*" in value:
            return None
        return {str(item) for item in value}

    def list_apps(self, request, provider=None, client_id=None):
        """Filter the (db ⊕ settings) app list by the tenant whitelist.

        The headless config's provider list (the login/signup buttons)
        derives from it via ``list_providers``, and allauth's own
        ``get_app`` selects from it when no per-tenant ``SocialApp``
        exists. The per-tenant branch of ``get_app`` returns before that
        fallback, so it applies the same whitelist itself.
        """
        apps = super().list_apps(
            request, provider=provider, client_id=client_id
        )
        allowed = self._allowed_providers(request)
        if allowed is None:
            return apps
        return [
            app for app in apps if (app.provider_id or app.provider) in allowed
        ]

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
        config (same fix pattern as
        ``pre_login``).
        """
        tenant = _resolve_tenant_from_request(request)
        if (
            tenant is not None
            and getattr(tenant, "schema_name", "public") != "public"
        ):
            from allauth.socialaccount.models import SocialApp

            allowed = self._allowed_providers(request)
            if allowed is not None and provider not in allowed:
                # Enforced here as well as in ``list_apps``: the per-tenant
                # lookup below returns before allauth's own ``get_app``
                # ever consults ``list_apps``, so a provider the merchant
                # switched off was hidden from the login buttons yet still
                # started OAuth when its redirect URL was hit directly.
                raise SocialApp.DoesNotExist()
            try:
                from django.contrib.sites.models import Site

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
        # No membership grant: a social signup creates the shopper in the
        # tenant's own schema, which is what scopes them to this store.
        # Membership is a staff grant held by platform-public identities.
        return super().save_user(request, sociallogin, form=form)


class TenantHeadlessAdapter(DefaultHeadlessAdapter):
    """Tenant-aware frontend URL rewriting for allauth's headless flows.

    ``allauth.core.internal.httpkit.get_frontend_url`` — the function
    password reset / signup / email-confirmation / social-login-error
    links actually go through — delegates to
    ``allauth.headless.adapter.get_adapter().get_frontend_url(...)``
    whenever ``allauth.headless`` is installed (it is here, see
    ``INSTALLED_APPS``). ``DefaultAccountAdapter`` (``ACCOUNT_ADAPTER``,
    i.e. ``TenantAccountAdapter``) has **no** ``get_frontend_url``
    method at all — it is a hook on the separate ``HEADLESS_ADAPTER``
    class hierarchy. Wire this class in via ``settings.HEADLESS_ADAPTER``
    so the rewrite actually runs.

    ``settings.HEADLESS_FRONTEND_URLS`` values are always absolute
    (built from ``NUXT_BASE_URL``), so the platform URL returned by
    ``super().get_frontend_url()`` already has a full scheme+host. This
    override swaps that scheme+host for the requesting tenant's primary
    domain, keeping path/query/fragment (and the allauth-substituted
    ``{key}``/etc placeholders) untouched. Falls through to the
    platform URL unchanged when no tenant is resolvable (public schema,
    admin routines, misconfiguration).

    Resolves the tenant from ``self.request.get_host()`` (via
    ``_resolve_tenant_from_request``), NOT ``connection.tenant`` — same
    H7 rationale as ``TenantAccountAdapter.pre_login`` /
    ``TenantSocialAccountAdapter.get_app``: under Daphne/Channels,
    ``database_sync_to_async`` thread pooling can leave a stale
    ``connection.tenant`` from an earlier request on the same thread.
    """

    def get_frontend_url(self, urlname: str, **kwargs) -> str | None:
        url = super().get_frontend_url(urlname, **kwargs)
        if not url:
            return url

        tenant = _resolve_tenant_from_request(self.request)
        if (
            tenant is None
            or getattr(tenant, "schema_name", "public") == "public"
        ):
            return url

        domain_obj = tenant.domains.filter(is_primary=True).first()
        domain = getattr(domain_obj, "domain", "") if domain_obj else ""
        if not domain:
            return url

        parsed = urlsplit(url)
        return urlunsplit(
            (
                _default_url_scheme(),
                domain,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
