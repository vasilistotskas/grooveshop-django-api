"""Access control for the email-template management surface.

Regression cover for the audit finding that these four views were
mounted on EVERY host (tenant + platform control plane) behind nothing
but ``staff_member_required``. ``is_staff`` is a global flag on the
shared ``UserAccount``, so that let a merchant staffer of store A read
store B's orders — the management page lists recent orders, and
``order/<id>/`` returns customer PII.

The fix has two halves, both asserted here:

1. Every view is wrapped in ``admin.site.admin_view`` so it runs
   ``MyAdminSite.has_permission`` (platform-staff session + per-tenant
   membership), not just the global staff flag.
2. The URLs are storefront-only. They query ``Order``, which lives in
   TENANT_APPS and has no table in the public schema, so serving them
   on the platform host was both a data-exposure path and a 500.
"""

import importlib

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.urls.resolvers import URLPattern, URLResolver

User = get_user_model()

EMAIL_TEMPLATE_VIEWS = [
    ("email_templates:management", (), {}),
    ("email_templates:preview", (), {}),
    ("email_templates:template_info", ("order_shipped",), {}),
    ("email_templates:order_data", (1,), {}),
]


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(
                pattern.url_patterns, prefix + str(pattern.pattern)
            )
        elif isinstance(pattern, URLPattern):
            callback = pattern.callback
            yield (
                prefix + str(pattern.pattern),
                getattr(callback, "__module__", ""),
            )


def _email_patterns(urlconf_module: str) -> list[str]:
    module = importlib.import_module(urlconf_module)
    return [
        url
        for url, mod in _walk(module.urlpatterns)
        if mod.startswith("core.email")
    ]


class TestEmailTemplateAdminMountPoints:
    """The URLconf half of the fix — structural, no DB needed."""

    def test_served_on_storefront_urlconf(self):
        assert len(_email_patterns("core.urls")) == 4

    def test_not_served_on_platform_control_plane(self):
        """The platform host must not expose a tenant-data surface.

        ``tenant.urls_public`` is the PUBLIC_SCHEMA_URLCONF. These views
        read ``Order``; in the public schema that table does not exist.
        """
        assert _email_patterns("tenant.urls_public") == []


@pytest.mark.django_db
class TestEmailTemplateAdminPermissions:
    """The permission half — a bare ``is_staff`` user is not enough."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email="staff@example.com",
            username="staffuser",
            password="testpass123",
            is_staff=True,
        )

    @pytest.mark.parametrize("name,args,kwargs", EMAIL_TEMPLATE_VIEWS)
    def test_anonymous_is_denied(self, name, args, kwargs):
        url = reverse(name, args=args, kwargs=kwargs)
        response = self.client.get(url)
        assert response.status_code in (301, 302)
        assert "login" in response["Location"]

    @pytest.mark.parametrize("name,args,kwargs", EMAIL_TEMPLATE_VIEWS)
    def test_plain_staff_without_platform_session_is_denied(
        self, name, args, kwargs
    ):
        """``is_staff`` alone must NOT open the page.

        ``force_login`` mints a session via ModelBackend, not
        ``PlatformStaffBackend``, so ``is_platform_staff_session`` is
        False and ``MyAdminSite.has_permission`` rejects — exactly the
        cross-tenant path this fix closes. Under the old
        ``staff_member_required`` gate every one of these returned 200.
        """
        self.client.force_login(self.staff)
        url = reverse(name, args=args, kwargs=kwargs)
        response = self.client.get(url)
        assert response.status_code in (301, 302), (
            f"{name} served content to a bare is_staff user "
            f"(status {response.status_code}) — the admin_view gate is missing"
        )
        assert "login" in response["Location"]

    def test_platform_staff_session_is_admitted(self):
        """The gate must DISCRIMINATE, not deny everyone.

        A superuser whose session was minted by ``PlatformStaffBackend``
        passes ``MyAdminSite.has_permission``, so the page renders. Without
        this the suite above would still pass against a page that is simply
        broken for everybody.
        """
        from django.contrib.auth import BACKEND_SESSION_KEY

        from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

        superuser = User.objects.create_superuser(
            email="root@example.com",
            username="rootuser",
            password="testpass123",
        )
        self.client.force_login(superuser)
        session = self.client.session
        session[BACKEND_SESSION_KEY] = PLATFORM_STAFF_BACKEND_PATH
        session.save()

        response = self.client.get(reverse("email_templates:management"))
        assert response.status_code == 200
