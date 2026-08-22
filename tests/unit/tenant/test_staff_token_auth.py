"""The staff token authenticator: public-schema, stamped, revocable.

The security properties Design B rests on (docs/api-staff-identity.md):
staff tokens resolve PUBLIC identities regardless of request host, the
returned user carries the provenance stamp the role backend requires,
the ``Bearer``/``StaffBearer`` keyword split keeps both authenticators
composable, and revocation kills outstanding tokens immediately.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework import exceptions

from core.api.tokens import KNOX_ABSOLUTE_MAX_AGE
from tenant.api_tokens import PlatformStaffTokenAuthentication
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import PlatformStaffToken
from user.factories.account import UserAccountFactory


def _mint(user):
    instance, token = PlatformStaffToken.objects.create(user)
    return instance, token


def _request(header: str | None):
    request = RequestFactory().get("/api/v1/product")
    if header is not None:
        request.META["HTTP_AUTHORIZATION"] = header
    return request


@pytest.mark.django_db
class TestStaffTokenAuthentication:
    def test_roundtrip(self):
        user = UserAccountFactory(is_staff=True)
        _, token = _mint(user)

        result = PlatformStaffTokenAuthentication().authenticate(
            _request(f"StaffBearer {token}")
        )
        assert result is not None
        auth_user, auth_token = result
        assert auth_user == user
        assert auth_token.user_id == user.pk

    def test_stamps_the_platform_identity(self):
        """The stamp is what TenantRolePermissionBackend grants to —
        without it a staff token would authenticate but hold nothing."""
        user = UserAccountFactory(is_staff=True)
        _, token = _mint(user)

        auth_user, _ = PlatformStaffTokenAuthentication().authenticate(
            _request(f"StaffBearer {token}")
        )
        assert getattr(auth_user, PLATFORM_IDENTITY_ATTR, False) is True

    def test_ignores_the_bearer_keyword(self):
        """Chain composition: a customer ``Bearer`` header must fall
        through (return None), never be swallowed or rejected here."""
        user = UserAccountFactory(is_staff=True)
        _, token = _mint(user)

        result = PlatformStaffTokenAuthentication().authenticate(
            _request(f"Bearer {token}")
        )
        assert result is None

    def test_absent_header_falls_through(self):
        assert (
            PlatformStaffTokenAuthentication().authenticate(_request(None))
            is None
        )

    def test_unknown_token_is_rejected(self):
        with pytest.raises(exceptions.AuthenticationFailed):
            PlatformStaffTokenAuthentication().authenticate(
                _request("StaffBearer not-a-real-token")
            )

    def test_customer_tokens_do_not_authenticate_here(self):
        """A knox customer token presented as StaffBearer must fail —
        the two tables are disjoint by construction."""
        from knox.models import AuthToken

        user = UserAccountFactory(is_staff=True)
        _, customer_token = AuthToken.objects.create(user)

        with pytest.raises(exceptions.AuthenticationFailed):
            PlatformStaffTokenAuthentication().authenticate(
                _request(f"StaffBearer {customer_token}")
            )

    def test_revocation_kills_outstanding_tokens(self):
        """The standing revocation flow clears is_staff; a live token
        must die with it, not at its next expiry."""
        user = UserAccountFactory(is_staff=True)
        _, token = _mint(user)

        user.is_staff = False
        user.save(update_fields=["is_staff"])

        with pytest.raises(exceptions.AuthenticationFailed):
            PlatformStaffTokenAuthentication().authenticate(
                _request(f"StaffBearer {token}")
            )

    def test_inactive_user_is_rejected(self):
        user = UserAccountFactory(is_staff=True)
        _, token = _mint(user)
        user.is_active = False
        user.save(update_fields=["is_active"])

        with pytest.raises(exceptions.AuthenticationFailed):
            PlatformStaffTokenAuthentication().authenticate(
                _request(f"StaffBearer {token}")
            )

    def test_absolute_age_cap_applies_to_staff_tokens(self):
        """Staff tokens inherit the customer cap, not a laxer one."""
        user = UserAccountFactory(is_staff=True)
        instance, token = _mint(user)
        PlatformStaffToken.objects.filter(pk=instance.pk).update(
            created=timezone.now() - KNOX_ABSOLUTE_MAX_AGE - timedelta(days=1),
            expiry=timezone.now() + timedelta(days=1),
        )

        with pytest.raises(exceptions.AuthenticationFailed):
            PlatformStaffTokenAuthentication().authenticate(
                _request(f"StaffBearer {token}")
            )
        assert not PlatformStaffToken.objects.filter(pk=instance.pk).exists()

    def test_challenge_names_the_staff_keyword(self):
        assert (
            PlatformStaffTokenAuthentication().authenticate_header(
                _request(None)
            )
            == "StaffBearer"
        )


@pytest.mark.django_db
class TestSchemaResidence:
    def test_the_token_table_is_shared_scope(self):
        """``tenant`` is in SHARED_APPS only, so the table exists in
        public alone — the structural mirror image of knox_authtoken.
        A membership in TENANT_APPS would silently break the whole
        identity model, so pin the app placement itself."""
        from django.conf import settings

        assert PlatformStaffToken._meta.app_label == "tenant"
        assert "tenant" in settings.SHARED_APPS
        assert "tenant" not in settings.TENANT_APPS

    def test_user_fk_targets_the_shared_user_model(self):
        field = PlatformStaffToken._meta.get_field("user")
        assert field.related_model._meta.label == "user.UserAccount"
        assert field.remote_field.related_name == "platform_staff_tokens"
