"""Tests for ``UserTenantMembershipAdmin.formfield_for_foreignkey``.

``UserAccount`` is mirrored per-schema — this admin only ever
references PUBLIC-schema users (platform identities), so the ``user``
FK dropdown must be pinned to that schema rather than whatever schema
the connection happens to be pinned to when the form is built.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.test import RequestFactory

from tenant.admin import UserTenantMembershipAdmin
from tenant.models import UserTenantMembership
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _admin():
    # A real, registered admin_site is required for FK fields that
    # fall through to Django's default formfield_for_foreignkey (e.g.
    # "tenant" below) — it calls self.admin_site.get_model_admin(...).
    return UserTenantMembershipAdmin(UserTenantMembership, admin_site)


def _user_field(model_admin, request):
    db_field = UserTenantMembership._meta.get_field("user")
    return model_admin.formfield_for_foreignkey(db_field, request)


class TestUserFieldQueryset:
    def test_queryset_contains_public_users(self):
        staff = UserAccountFactory(is_staff=True)
        request = RequestFactory().get(
            "/admin/tenant/usertenantmembership/add/"
        )
        field = _user_field(_admin(), request)
        assert staff in field.queryset

    def test_queryset_is_scoped_to_user_model(self):
        request = RequestFactory().get(
            "/admin/tenant/usertenantmembership/add/"
        )
        field = _user_field(_admin(), request)
        assert field.queryset.model is UserAccountFactory._meta.model

    def test_other_fk_fields_use_default_behaviour(self):
        """Only ``user`` gets the schema pin — ``tenant`` keeps the
        default ``formfield_for_foreignkey`` behaviour."""
        request = RequestFactory().get(
            "/admin/tenant/usertenantmembership/add/"
        )
        db_field = UserTenantMembership._meta.get_field("tenant")
        field = _admin().formfield_for_foreignkey(db_field, request)
        assert field is not None
