"""The platform account admin's store-role inline.

A store role is a staff grant. The model's default, MEMBER, is reserved
and no longer issued: a role added without changing the select was
active yet granted no access to the store's admin. The inline offers the
roles still issued and starts a new one at STAFF.
"""

from __future__ import annotations

import pytest

from tenant.models import TenantMembershipRole
from user.admin import TenantMembershipInlineForm

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
