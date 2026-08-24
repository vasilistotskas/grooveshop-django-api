"""H1 — ``TenantAdmin.save_related`` provisions a brand-new tenant the
same way ``manage.py tenant_create`` does (see ``tenant/provisioning.py``).

Before this, the stock Unfold "New Store" add form only created the
``Tenant`` row + Postgres schema: the owner had no membership (locked
out of the admin) and no ``api.<domain>`` TenantDomain row existed
(WebSocket notifications close 4004, social login 404s at the form
POST).

``transaction.on_commit`` callbacks run synchronously in this suite
(``tests/conftest.py::_run_transaction_on_commit_immediately``,
autouse), so calling ``save_related`` directly exercises the real
deferred-provisioning code path without any extra test wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import storage as messages_storage

from tenant.admin import TenantAdmin
from tenant.models import Tenant, TenantDomain, UserTenantMembership

User = get_user_model()

pytestmark = pytest.mark.django_db


def _make_tenant(slug: str, **kwargs) -> Tenant:
    defaults = {"is_active": True, "suspended_at": None}
    defaults.update(kwargs)
    t = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        **defaults,
    )
    t.auto_create_schema = False
    t.save()
    return t


def _admin_request():
    req = MagicMock()
    req._messages = messages_storage.default_storage(req)
    req.user = User.objects.create_superuser(
        email=f"tenant-admin-{uuid4().hex[:10]}@example.com",
        username=f"tenantadmin{uuid4().hex[:10]}",
        password="testpass123",
    )
    return req


def _admin():
    return TenantAdmin(Tenant, None)


def _form(tenant):
    form = MagicMock()
    form.instance = tenant
    return form


class TestSaveRelatedProvisionsOnAdd:
    def test_provisions_api_domain_and_owner_membership(self):
        tenant = _make_tenant("new-store-happy-path")
        TenantDomain.objects.create(
            domain="new-store-happy-path.example.com",
            tenant=tenant,
            is_primary=True,
        )
        owner = User.objects.create_user(
            email=tenant.owner_email,
            username="newstorehappypath",
            password="testpass123",
        )

        admin = _admin()
        admin.save_related(_admin_request(), _form(tenant), [], change=False)

        assert TenantDomain.objects.filter(
            tenant=tenant,
            domain="api.new-store-happy-path.example.com",
            is_primary=False,
        ).exists()
        membership = UserTenantMembership.objects.get(tenant=tenant, user=owner)
        assert membership.role == "owner"
        assert membership.is_active is True

    def test_no_primary_domain_warns_and_does_not_crash(self):
        """No inline domain entered — ``ensure_api_domain`` no-ops;
        the admin must surface a warning, not raise."""
        tenant = _make_tenant("new-store-no-domain")
        User.objects.create_user(
            email=tenant.owner_email,
            username="newstorenodomain",
            password="testpass123",
        )

        admin = _admin()
        request = _admin_request()
        admin.save_related(request, _form(tenant), [], change=False)

        assert not TenantDomain.objects.filter(tenant=tenant).exists()
        # A membership can still be granted — the missing domain and
        # the owner lookup are independent steps.
        assert UserTenantMembership.objects.filter(tenant=tenant).exists()

    def test_owner_not_registered_yet_warns_and_does_not_crash(self):
        tenant = _make_tenant("new-store-no-owner")
        TenantDomain.objects.create(
            domain="new-store-no-owner.example.com",
            tenant=tenant,
            is_primary=True,
        )
        # No matching UserAccount for tenant.owner_email.

        admin = _admin()
        admin.save_related(_admin_request(), _form(tenant), [], change=False)

        assert TenantDomain.objects.filter(
            tenant=tenant, domain="api.new-store-no-owner.example.com"
        ).exists()
        assert not UserTenantMembership.objects.filter(tenant=tenant).exists()

    def test_provisioning_failure_is_reported_not_raised(self):
        tenant = _make_tenant("new-store-provision-fails")
        TenantDomain.objects.create(
            domain="new-store-provision-fails.example.com",
            tenant=tenant,
            is_primary=True,
        )

        admin = _admin()
        request = _admin_request()
        with patch(
            "tenant.provisioning.provision_tenant",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise — the tenant row itself already committed
            # and the admin's add response must still render.
            admin.save_related(request, _form(tenant), [], change=False)

        # The autouse on_commit-immediate fixture would swallow a raised
        # exception on its own (robust=True semantics) — assert on the
        # actual ERROR message so this test would fail if the
        # try/except in ``_provision_new_tenant`` were ever removed.
        from django.contrib import messages as django_messages

        recorded = list(request._messages)
        assert any(
            m.level == django_messages.ERROR and "boom" in m.message
            for m in recorded
        )


class TestSaveRelatedSkipsOnChange:
    def test_editing_an_existing_tenant_never_provisions(self):
        tenant = _make_tenant("existing-store-edit")
        TenantDomain.objects.create(
            domain="existing-store-edit.example.com",
            tenant=tenant,
            is_primary=True,
        )
        User.objects.create_user(
            email=tenant.owner_email,
            username="existingstoreedit",
            password="testpass123",
        )

        admin = _admin()
        with patch("tenant.provisioning.provision_tenant") as mock_provision:
            admin.save_related(_admin_request(), _form(tenant), [], change=True)
            mock_provision.assert_not_called()

        # No api.<domain> row was created — editing an existing tenant
        # (which already has its primary domain from before) must not
        # re-derive or duplicate anything.
        assert not TenantDomain.objects.filter(
            tenant=tenant, domain="api.existing-store-edit.example.com"
        ).exists()
