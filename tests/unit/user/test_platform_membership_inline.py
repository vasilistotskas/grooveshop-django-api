"""The platform account admin's store-role inline.

A store role is a staff grant. The model's default, MEMBER, is reserved
and no longer issued: a role added without changing the select was
active yet granted no access to the store's admin. The inline offers the
roles still issued and starts a new one at STAFF. Removing a row refuses
to strip a store of its last owner.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.test import RequestFactory
from django.utils.translation import gettext

from tenant.models import TenantMembershipRole, UserTenantMembership
from tests.utils.staff import store_tenant
from user.admin import TenantMembershipInline, TenantMembershipInlineForm
from user.factories.account import UserAccountFactory
from user.models import UserAccount

pytestmark = pytest.mark.django_db


def test_offers_only_the_roles_still_issued():
    choices = TenantMembershipInlineForm().fields["role"].choices

    assert [value for value, _label in choices] == [
        TenantMembershipRole.STAFF,
        TenantMembershipRole.ADMIN,
        TenantMembershipRole.OWNER,
    ]


def test_a_new_role_starts_as_staff():
    assert TenantMembershipInlineForm()["role"].value() == (
        TenantMembershipRole.STAFF
    )


def _formset_class():
    request = RequestFactory().get("/admin/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    inline = TenantMembershipInline(UserAccount, admin_site)
    return inline.get_formset(request)


def _post(member, *, delete):
    prefix = _formset_class().get_default_prefix()
    return {
        f"{prefix}-TOTAL_FORMS": "1",
        f"{prefix}-INITIAL_FORMS": "1",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
        f"{prefix}-0-id": str(member.pk),
        f"{prefix}-0-user": str(member.user_id),
        f"{prefix}-0-tenant": str(member.tenant_id),
        f"{prefix}-0-role": member.role,
        f"{prefix}-0-is_active": "on",
        **({f"{prefix}-0-DELETE": "on"} if delete else {}),
    }


def _seat(tenant, role):
    return UserTenantMembership.objects.create(
        user=UserAccountFactory(is_staff=True), tenant=tenant, role=role
    )


def test_deleting_the_last_owner_row_is_a_form_error():
    owner = _seat(store_tenant("inline_last_owner"), TenantMembershipRole.OWNER)

    formset = _formset_class()(_post(owner, delete=True), instance=owner.user)

    assert not formset.is_valid()
    assert formset.non_form_errors() == [
        gettext("A store must keep at least one active owner.")
    ]


def test_deleting_an_owner_row_is_allowed_while_another_remains():
    tenant = store_tenant("inline_two_owners")
    owner = _seat(tenant, TenantMembershipRole.OWNER)
    _seat(tenant, TenantMembershipRole.OWNER)

    formset = _formset_class()(_post(owner, delete=True), instance=owner.user)

    assert formset.is_valid()
