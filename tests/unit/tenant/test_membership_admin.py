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

from tenant.admin import (
    PublicSchemaModelChoiceField,
    UserTenantMembershipAdmin,
)
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
        """Only ``user`` gets the schema pin.

        ``tenant`` keeps the default behaviour on the PLATFORM console,
        which is what this request represents. It IS narrowed to the
        operator's own store on a tenant host — see
        ``test_admin_self_service.py`` — so that a store ADMIN cannot
        grant themselves membership in someone else's store.
        """
        request = RequestFactory().get(
            "/admin/tenant/usertenantmembership/add/"
        )
        db_field = UserTenantMembership._meta.get_field("tenant")
        field = _admin().formfield_for_foreignkey(db_field, request)
        assert field is not None


class TestUserFieldSchemaPinOnTenantHost:
    """On a tenant host the ``user`` field must be pinned to the public
    schema so it lists platform identities, never that store's shoppers
    (whose colliding pks would misbind the membership)."""

    def _request(self):
        return RequestFactory().get("/admin/tenant/usertenantmembership/add/")

    def test_field_is_public_pinned_on_a_tenant_host(self):
        from unittest import mock

        with mock.patch(
            "tenant.admin.self_service_tenant", return_value=object()
        ):
            field = _user_field(_admin(), self._request())
        assert isinstance(field, PublicSchemaModelChoiceField)

    def test_field_is_plain_on_the_platform_host(self):
        from unittest import mock

        with mock.patch("tenant.admin.self_service_tenant", return_value=None):
            field = _user_field(_admin(), self._request())
        assert not isinstance(field, PublicSchemaModelChoiceField)

    def test_autocomplete_for_user_is_dropped_on_a_tenant_host(self):
        from unittest import mock

        with mock.patch(
            "tenant.admin.self_service_tenant", return_value=object()
        ):
            fields = _admin().get_autocomplete_fields(self._request())
        assert "user" not in fields
        assert "tenant" in fields

    def test_autocomplete_for_user_is_kept_on_the_platform_host(self):
        from unittest import mock

        with mock.patch("tenant.admin.self_service_tenant", return_value=None):
            fields = _admin().get_autocomplete_fields(self._request())
        assert "user" in fields


class TestPublicSchemaModelChoiceFieldPins:
    """The field wraps every DB touch in schema_context(public)."""

    def _field(self):
        from django.contrib.auth import get_user_model

        return PublicSchemaModelChoiceField(
            queryset=get_user_model().objects.all()
        )

    def test_to_python_enters_public_schema(self):
        from unittest import mock

        field = self._field()
        with mock.patch("tenant.admin._public_schema_context") as ctx:
            field.to_python("")  # empty value still wraps the call
        ctx.assert_called_once()

    def test_valid_value_enters_public_schema(self):
        from unittest import mock

        staff = UserAccountFactory(is_staff=True)
        field = self._field()
        with mock.patch("tenant.admin._public_schema_context") as ctx:
            field.valid_value(staff)
        assert ctx.called

    def test_to_python_resolves_a_public_user(self):
        staff = UserAccountFactory(is_staff=True)
        field = self._field()
        assert field.to_python(str(staff.pk)) == staff
