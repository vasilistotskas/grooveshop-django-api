"""Tests for TenantAccountAdapter + TenantSocialAccountAdapter.

Covers the two behaviors the adapters add on top of the upstream
UserAccountAdapter / SocialAccountAdapter:

1. ``pre_login`` rejects a user without an active membership in the
   current tenant. This is the core authorization gate — without it a
   user registered on tenant A could sign into tenant B with the same
   credentials and read tenant B's data.
2. ``save_user`` creates a MEMBER membership as part of signup so a
   freshly registered user can immediately log in on the same tenant.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from tenant.allauth_adapter import (
    TenantAccountAdapter,
    TenantHeadlessAdapter,
    TenantSocialAccountAdapter,
)
from tenant.models import (
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="bob-adapter",
        email="bob-adapter@example.com",
        password="p",  # noqa: S106
    )


class TestPreLoginHasNoMembershipGate:
    """Login must not require a ``UserTenantMembership``.

    Customers are per-schema: ``user`` sits in both SHARED_APPS and
    TENANT_APPS and the tenant copy wins on the search path, so a
    shopper registered at tenant A has no row, no allauth records and no
    Knox token in tenant B — credential lookup fails there before any
    gate would run. The membership table meanwhile lives in the public
    schema with an FK to the public user table, so a tenant-schema
    shopper cannot hold one at all: the old gate rejected every
    legitimate customer, and the signup-time grant that tried to satisfy
    it raised ForeignKeyViolation.
    """

    @pytest.mark.django_db
    def test_allows_login_without_any_membership(self, tenant_factory, user):
        tenant = tenant_factory("prelogin-nomembership")
        # No membership row exists for this user anywhere.
        assert not UserTenantMembership.objects.filter(user=user).exists()

        adapter = TenantAccountAdapter()
        # Must not raise — the old implementation raised ValidationError
        # with "You do not have access to this store."
        with patch(
            "tenant.allauth_adapter._resolve_tenant_from_request",
            return_value=tenant,
        ):
            adapter.pre_login(
                MagicMock(),
                user,
                email_verification=None,
                signal_kwargs=None,
                email=user.email,
                signup=False,
                redirect_url=None,
            )

    @pytest.mark.django_db
    def test_allows_login_with_inactive_membership(self, tenant_factory, user):
        """An inactive STAFF grant is irrelevant to shopper login."""
        tenant = tenant_factory("prelogin-inactive")
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )
        with patch(
            "tenant.allauth_adapter._resolve_tenant_from_request",
            return_value=tenant,
        ):
            TenantAccountAdapter().pre_login(
                MagicMock(),
                user,
                email_verification=None,
                signal_kwargs=None,
                email=user.email,
                signup=False,
                redirect_url=None,
            )


class TestSignupGrantsNoMembership:
    """Signup creates the shopper in the tenant schema and stops there.

    The grant this replaces is the exact statement that 500'd: it
    inserted into a public-schema table keyed by a public user id, for a
    user that exists only in the tenant schema.
    """

    @pytest.mark.django_db
    def test_email_signup_creates_no_membership(
        self, tenant_factory, bind_tenant, monkeypatch
    ):
        tenant = tenant_factory("signup-email")
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        new_user = User.objects.create_user(
            username="new-signup",
            email="new-signup@example.com",
            password="p",  # noqa: S106
        )

        monkeypatch.setattr(
            "user.adapter.UserAccountAdapter.save_user",
            lambda self, request, user, form, commit=True: user,
        )

        returned = adapter.save_user(
            request=MagicMock(),
            user=new_user,
            form=MagicMock(),
            commit=True,
        )

        assert returned.pk == new_user.pk
        assert not UserTenantMembership.objects.filter(
            user=new_user, tenant=tenant
        ).exists()

    @pytest.mark.django_db
    def test_social_signup_creates_no_membership(
        self, tenant_factory, bind_tenant, monkeypatch
    ):
        tenant = tenant_factory("signup-social")
        bind_tenant(tenant)

        new_user = User.objects.create_user(
            username="social-new",
            email="social-new@example.com",
            password="p",  # noqa: S106
        )
        monkeypatch.setattr(
            "user.adapter.SocialAccountAdapter.save_user",
            lambda self, request, sociallogin, form=None: new_user,
        )

        returned = TenantSocialAccountAdapter().save_user(
            request=MagicMock(), sociallogin=MagicMock()
        )

        assert returned.pk == new_user.pk
        assert not UserTenantMembership.objects.filter(
            user=new_user, tenant=tenant
        ).exists()


class TestTenantAwareEmailFormatting:
    """``UserAccountAdapter.format_email_subject`` / ``get_from_email``.

    ``TenantAccountAdapter`` (``settings.ACCOUNT_ADAPTER``) inherits both
    from ``UserAccountAdapter`` without overriding them, so exercising
    the concrete adapter class here covers the adapter actually wired
    up for allauth account emails (signup confirmation, password reset,
    MFA recovery codes, …).
    """

    @pytest.mark.django_db
    def test_format_email_subject_uses_tenant_store_name(
        self, tenant_factory, bind_tenant
    ):
        tenant = tenant_factory("adapter-subject-1")
        tenant.store_name = "Branded Store"
        tenant.save()
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        assert (
            adapter.format_email_subject("Confirm your email")
            == "[Branded Store] Confirm your email"
        )

    @pytest.mark.django_db
    def test_format_email_subject_falls_back_to_settings_when_no_tenant(
        self, monkeypatch, settings
    ):
        from django.db import connection

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.SITE_NAME = "GrooveShop"

        adapter = TenantAccountAdapter()
        assert (
            adapter.format_email_subject("Confirm your email")
            == "[GrooveShop] Confirm your email"
        )

    @pytest.mark.django_db
    def test_get_from_email_uses_tenant_from_email(
        self, tenant_factory, bind_tenant
    ):
        tenant = tenant_factory("adapter-from-1")
        tenant.from_email = "shop@brand.com"
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        # DMARC safety: platform-authenticated address + tenant display
        # name; the merchant address is never the From on the platform
        # relay (see tenant_from_email).
        assert adapter.get_from_email().endswith(">")
        assert "Brand Shop" in adapter.get_from_email()
        assert "shop@brand.com" not in adapter.get_from_email()

    @pytest.mark.django_db
    def test_get_from_email_falls_back_to_default_from_email(
        self, tenant_factory, bind_tenant, settings
    ):
        tenant = tenant_factory("adapter-from-2")
        tenant.from_email = ""
        tenant.store_name = "Adapter Store"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"

        adapter = TenantAccountAdapter()
        assert (
            adapter.get_from_email() == "Adapter Store <noreply@platform.com>"
        )


class TestSaveUserInPublicSchema:
    @pytest.mark.django_db
    def test_no_membership_when_public_schema(
        self, user, bind_tenant, monkeypatch
    ):
        # Signups on public schema (platform admin routines) do not get
        # tenant memberships — there is no tenant to attach them to.
        bind_tenant(SimpleNamespace(schema_name="public"))

        monkeypatch.setattr(
            "user.adapter.UserAccountAdapter.save_user",
            lambda self, request, user, form, commit=True: user,
        )

        adapter = TenantAccountAdapter()
        adapter.save_user(
            request=MagicMock(),
            user=user,
            form=MagicMock(),
            commit=True,
        )

        assert UserTenantMembership.objects.filter(user=user).count() == 0


class TestHeadlessGetFrontendUrl:
    """``TenantHeadlessAdapter.get_frontend_url`` is the hook password
    reset / signup / email-confirmation / social-login-error links
    actually go through (``allauth.core.internal.httpkit.
    get_frontend_url`` delegates to ``HEADLESS_ADAPTER``, NOT
    ``ACCOUNT_ADAPTER`` — see the class docstring). These tests exercise
    it end-to-end through allauth's real ``default_get_frontend_url``
    machinery rather than mocking it, so a settings/wiring regression
    (e.g. ``HEADLESS_ADAPTER`` pointing at the wrong class) would show
    up here.
    """

    @staticmethod
    def _adapter_with_host(host: str) -> TenantHeadlessAdapter:
        adapter = TenantHeadlessAdapter()
        request = MagicMock()
        request.get_host.return_value = host
        adapter.request = request
        return adapter

    @pytest.mark.django_db
    def test_platform_context_returns_url_unchanged(self, settings):
        settings.HEADLESS_FRONTEND_URLS = {
            "account_reset_password_from_key": (
                "https://platform.example/account/password/reset/key/{key}"
            ),
        }
        # Host doesn't match any TenantDomain row — no tenant resolvable.
        adapter = self._adapter_with_host("unmatched-host.example")

        url = adapter.get_frontend_url(
            "account_reset_password_from_key", key="abc123"
        )

        assert (
            url == "https://platform.example/account/password/reset/key/abc123"
        )

    @pytest.mark.django_db
    def test_tenant_context_rewrites_scheme_and_host_keeps_path_and_query(
        self, tenant_factory, settings
    ):
        settings.ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
        settings.HEADLESS_FRONTEND_URLS = {
            "account_reset_password_from_key": (
                "https://platform.example/account/password/reset/key/{key}?x=1"
            ),
        }
        tenant = tenant_factory("headless-reset")
        TenantDomain.objects.create(
            tenant=tenant, domain="tenant-b.example", is_primary=True
        )
        adapter = self._adapter_with_host("tenant-b.example")

        url = adapter.get_frontend_url(
            "account_reset_password_from_key", key="abc123"
        )

        assert url == (
            "https://tenant-b.example/account/password/reset/key/abc123?x=1"
        )

    @pytest.mark.django_db
    def test_public_schema_tenant_returns_url_unchanged(
        self, settings, monkeypatch
    ):
        # A resolvable tenant whose schema IS public (edge case — the
        # platform's own host resolving to a TenantDomain row) must not
        # be rewritten; public is the platform itself.
        settings.HEADLESS_FRONTEND_URLS = {
            "account_signup": "https://platform.example/account/signup",
        }
        monkeypatch.setattr(
            "tenant.allauth_adapter._resolve_tenant_from_request",
            lambda request: SimpleNamespace(schema_name="public"),
        )
        adapter = self._adapter_with_host("platform.example")

        url = adapter.get_frontend_url("account_signup")

        assert url == "https://platform.example/account/signup"

    @pytest.mark.django_db
    def test_no_tenant_domain_row_returns_url_unchanged(
        self, tenant_factory, settings
    ):
        # Tenant resolves but has no primary domain row (defensive —
        # shouldn't happen in practice, mirrors get_tenant_base_url's
        # fallback behaviour).
        settings.HEADLESS_FRONTEND_URLS = {
            "account_signup": "https://platform.example/account/signup",
        }
        tenant = tenant_factory("headless-no-domain")
        adapter = self._adapter_with_host("irrelevant.example")

        with patch(
            "tenant.allauth_adapter._resolve_tenant_from_request",
            return_value=tenant,
        ):
            url = adapter.get_frontend_url("account_signup")

        assert url == "https://platform.example/account/signup"

    def test_returns_none_when_headless_frontend_urls_missing_and_not_headless_only(
        self, settings
    ):
        settings.HEADLESS_FRONTEND_URLS = {}
        settings.HEADLESS_ONLY = False
        adapter = self._adapter_with_host("unmatched-host.example")

        assert adapter.get_frontend_url("account_signup") is None


class TestSocialLoginProviderFilter:
    """SOCIAL_LOGIN_PROVIDERS drives TenantSocialAccountAdapter.list_apps."""

    def _allowed(self, setting_value, schema_name="shop"):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import patch

        from tenant.allauth_adapter import TenantSocialAccountAdapter

        tenant = SimpleNamespace(schema_name=schema_name)
        with (
            patch(
                "tenant.allauth_adapter._resolve_tenant_from_request",
                return_value=tenant,
            ),
            patch(
                "django_tenants.utils.schema_context",
                return_value=nullcontext(),
            ),
            patch(
                "extra_settings.models.Setting.get",
                return_value=setting_value,
            ),
        ):
            return TenantSocialAccountAdapter._allowed_providers(object())

    def test_star_means_no_restriction(self):
        assert self._allowed(["*"]) is None

    def test_unset_means_no_restriction(self):
        assert self._allowed(None) is None

    def test_subset_whitelists(self):
        assert self._allowed(["google"]) == {"google"}

    def test_empty_list_disables_all(self):
        assert self._allowed([]) == set()

    def test_public_schema_never_restricts(self):
        assert self._allowed(["google"], schema_name="public") is None
