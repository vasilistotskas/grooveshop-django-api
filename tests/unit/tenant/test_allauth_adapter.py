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
from unittest.mock import MagicMock

import pytest
from django import forms
from django.contrib.auth import get_user_model

from tenant.allauth_adapter import (
    TenantAccountAdapter,
    TenantSocialAccountAdapter,
)
from tenant.models import (
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


class TestPreLoginMembershipGate:
    @pytest.mark.django_db
    def test_allows_login_with_active_membership(
        self, tenant_factory, user, bind_tenant
    ):
        tenant = tenant_factory("prelogin-allow")
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=True,
        )
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        # pre_login calls super().pre_login with extra kwargs; patch the
        # super chain so we isolate the membership check.
        adapter.pre_login = adapter.__class__.pre_login.__get__(adapter)

        request = MagicMock()
        # Should not raise
        adapter.pre_login(
            request,
            user,
            email_verification=None,
            signal_kwargs=None,
            email="bob-adapter@example.com",
            signup=False,
            redirect_url=None,
        )

    @pytest.mark.django_db
    def test_rejects_login_without_membership(
        self, tenant_factory, user, bind_tenant
    ):
        tenant = tenant_factory("prelogin-reject")
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        request = MagicMock()

        with pytest.raises(forms.ValidationError) as exc:
            adapter.pre_login(
                request,
                user,
                email_verification=None,
                signal_kwargs=None,
                email="bob-adapter@example.com",
                signup=False,
                redirect_url=None,
            )
        # The code is what the storefront i18n pack keys on — preserve it
        assert exc.value.code == "no_tenant_membership"

    @pytest.mark.django_db
    def test_rejects_login_with_inactive_membership(
        self, tenant_factory, user, bind_tenant
    ):
        tenant = tenant_factory("prelogin-inactive")
        UserTenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantMembershipRole.MEMBER,
            is_active=False,
        )
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        request = MagicMock()

        with pytest.raises(forms.ValidationError):
            adapter.pre_login(
                request,
                user,
                email_verification=None,
                signal_kwargs=None,
                email="bob-adapter@example.com",
                signup=False,
                redirect_url=None,
            )

    @pytest.mark.django_db
    def test_skips_check_on_public_schema(self, user, bind_tenant):
        # Platform admin paths (/admin/login/ on the public schema) must
        # still work. The gate short-circuits when schema_name == "public".
        bind_tenant(SimpleNamespace(schema_name="public"))

        adapter = TenantAccountAdapter()
        request = MagicMock()

        # Will call super().pre_login which expects more context; just
        # verify we don't raise ValidationError for missing membership.
        try:
            adapter.pre_login(
                request,
                user,
                email_verification=None,
                signal_kwargs=None,
                email="bob-adapter@example.com",
                signup=False,
                redirect_url=None,
            )
        except forms.ValidationError:
            pytest.fail("public schema should skip the tenant membership gate")
        except Exception:
            # Super's pre_login may raise for unrelated reasons given
            # our mock request; that's not what this test covers.
            pass

    @pytest.mark.django_db
    def test_skips_check_when_no_tenant(self, user, bind_tenant):
        bind_tenant(None)

        adapter = TenantAccountAdapter()
        request = MagicMock()

        try:
            adapter.pre_login(
                request,
                user,
                email_verification=None,
                signal_kwargs=None,
                email="bob-adapter@example.com",
                signup=False,
                redirect_url=None,
            )
        except forms.ValidationError:
            pytest.fail("no tenant should skip the membership gate")
        except Exception:
            pass


class TestSaveUserCreatesMembership:
    @pytest.mark.django_db
    def test_email_signup_creates_member_membership(
        self, tenant_factory, bind_tenant, monkeypatch
    ):
        tenant = tenant_factory("signup-email")
        bind_tenant(tenant)

        # Short-circuit the super().save_user call since it depends on
        # a real allauth form; we only want to test the membership side
        # effect in ``TenantAccountAdapter.save_user``.
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
        membership = UserTenantMembership.objects.get(
            user=new_user, tenant=tenant
        )
        assert membership.role == TenantMembershipRole.MEMBER
        assert membership.is_active is True

    @pytest.mark.django_db
    def test_email_signup_noop_when_commit_false(
        self, tenant_factory, bind_tenant, monkeypatch
    ):
        tenant = tenant_factory("signup-nocommit")
        bind_tenant(tenant)

        adapter = TenantAccountAdapter()
        new_user = User(
            username="transient",
            email="transient@example.com",
        )

        monkeypatch.setattr(
            "user.adapter.UserAccountAdapter.save_user",
            lambda self, request, user, form, commit=True: user,
        )

        adapter.save_user(
            request=MagicMock(),
            user=new_user,
            form=MagicMock(),
            commit=False,
        )

        assert UserTenantMembership.objects.filter(tenant=tenant).count() == 0

    @pytest.mark.django_db
    def test_social_signup_creates_member_membership(
        self, tenant_factory, bind_tenant, monkeypatch
    ):
        tenant = tenant_factory("social-signup")
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

        adapter = TenantSocialAccountAdapter()
        returned = adapter.save_user(
            request=MagicMock(), sociallogin=MagicMock()
        )

        assert returned.pk == new_user.pk
        assert UserTenantMembership.objects.filter(
            user=new_user, tenant=tenant
        ).exists()


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
