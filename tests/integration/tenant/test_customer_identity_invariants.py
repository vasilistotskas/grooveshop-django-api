"""Invariants that keep customer signup working on a tenant host.

The bug these guard against reached a deployed build and was invisible
to the whole suite:

``user`` is in BOTH ``SHARED_APPS`` and ``TENANT_APPS``, so with
``search_path = "<tenant>", public`` a signup arriving on a tenant
domain writes the account into the TENANT schema. ``tenant`` is
SHARED-only, so ``tenant_usertenantmembership`` exists only in public
and its ``user_id`` FK targets ``public.user_useraccount``. The signup
adapter then granted a membership — an insert that cannot succeed,
because the new user has no public row.

Live result: HTTP 500 with the account already written, an orphan that
could never log in and whose email could never be reused.

The suite missed it because every fixture builds tenants with
``auto_create_schema = False`` and creates users while the connection
is still in the public schema, where the FK is trivially satisfied.
These tests therefore assert the STRUCTURE rather than replaying a
request: they fail the moment the incompatible combination returns.
"""

from __future__ import annotations

import ast
import inspect

from django.conf import settings


def _app_in(app_list, label: str) -> bool:
    return any(a == label or a.endswith(f".{label}") for a in app_list)


def _referenced_names(module) -> set[str]:
    """Every identifier the module's CODE references.

    Parsed rather than grepped: docstrings in these modules explain the
    history of the removed gates and legitimately name them, so a text
    search would match its own explanation.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


class TestSchemaPlacementInvariants:
    def test_user_is_tenant_scoped(self):
        """Customers live in their tenant's schema — that is what scopes
        them to a store, and why no membership row is needed."""
        assert _app_in(settings.TENANT_APPS, "user")

    def test_membership_table_is_public_only(self):
        """``tenant`` must stay SHARED-only: membership is platform data.

        This is the other half of the incompatibility. If it ever gains
        a tenant-schema copy, revisit every FK in it.
        """
        assert _app_in(settings.SHARED_APPS, "tenant")
        assert not _app_in(settings.TENANT_APPS, "tenant")

    def test_sessions_are_tenant_scoped(self):
        """Sessions must be per-schema for the same reason users are.

        ``_auth_user_id`` is resolved against whichever schema serves the
        request. One shared table means a cookie minted on one host is
        accepted on another and resolved against a different user table,
        where the same id is a different person. The cookie Domain is not
        a boundary — it is scoped to the registrable domain, so every
        host under it receives the cookie.
        """
        assert _app_in(settings.TENANT_APPS, "sessions")


class TestSignupGrantsNoMembership:
    """No signup path may write a membership row.

    Asserted at the source level: a tenant-host signup has no public
    user id to key such a row by, so any insert here is the 500 coming
    back.
    """

    def test_allauth_adapters_never_touch_membership(self):
        import tenant.allauth_adapter as adapter

        names = _referenced_names(adapter)
        assert "UserTenantMembership" not in names
        assert "_ensure_member_membership" not in names


class TestCustomerAccessIsNotMembershipGated:
    """Nothing on the customer path may demand a membership.

    Each of these gates rejected every ordinary shopper: they required a
    row that only a platform-public identity can hold.
    """

    def test_knox_auth_does_not_check_membership(self):
        import core.api.tokens as tokens

        source = inspect.getsource(tokens)
        code = "\n".join(
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "user_has_tenant_access" not in code

    def test_agent_views_do_not_check_membership(self):
        import agent.views as views

        assert "HasTenantAccess" not in _referenced_names(views)

    def test_default_permission_is_not_membership_gated(self):
        assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
            "rest_framework.permissions.IsAuthenticatedOrReadOnly"
        ]
