"""Destroying a tenant asks first, on a page built from Unfold's
components, and the page re-posts the same selection."""

from __future__ import annotations

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tenant.models import Tenant
from tests.utils.staff import store_tenant


@override_settings(ROOT_URLCONF="tenant.urls_public")
class TestDestroyConfirmation(TestCase):
    @classmethod
    def setUpTestData(cls):
        from user.models import UserAccount

        cls.user = UserAccount.objects.create_superuser(
            email="destroyer@example.com",
            username="destroyer",
            password="x",
        )
        cls.store = store_tenant("destroy_confirm", name="Doomed Store")

    def test_the_first_post_only_asks(self):
        client = Client()
        client.force_login(
            self.user, backend="tenant.auth_backends.PlatformStaffBackend"
        )

        response = client.post(
            reverse("platform_admin:tenant_tenant_changelist"),
            {
                "action": "destroy_tenants",
                ACTION_CHECKBOX_NAME: [str(self.store.pk)],
            },
        )

        assert response.status_code == 200
        html = response.content.decode()
        assert "Doomed Store (destroy_confirm)" in html
        assert 'name="destroy_confirmed" value="yes"' in html
        assert f'value="{self.store.pk}"' in html
        assert Tenant.objects.filter(pk=self.store.pk).exists()
