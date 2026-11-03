"""The staff session's ``role`` is published as the roles it can carry."""

from __future__ import annotations

from builder import staff_session
from tenant.models import TenantMembershipRole


def test_role_is_an_enum_of_every_role_a_session_can_carry(openapi_schema):
    schemas = openapi_schema["components"]["schemas"]
    ref = schemas["StaffSessionStaff"]["properties"]["role"]["$ref"]
    assert ref.endswith("/StaffSessionRoleEnum")

    published = schemas[ref.rsplit("/", 1)[-1]]["enum"]

    assert published == [*TenantMembershipRole.values, "superuser"]
    assert set(published) == set(staff_session.ROLES)
