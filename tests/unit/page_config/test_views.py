from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from page_config.models import (
    ComponentType,
    ContentPage,
    PageLayout,
    PageSection,
)
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from user.factories.account import UserAccountFactory


def _make_content_page(slug, title, *, is_published, body="<p>Body</p>"):
    page = ContentPage.objects.create(slug=slug, is_published=is_published)
    page.set_current_language("el")
    page.title = title
    page.body = body
    page.save()
    return page


class TestPublicPageConfig(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.layout = PageLayout.objects.create(
            page_type="home",
            title="Homepage",
            is_published=True,
            published_at=timezone.now(),
        )
        self.section1 = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.HERO_CAROUSEL,
            title="Carousel",
            sort_order=0,
        )
        self.section2 = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.PRODUCTS_GRID,
            title="Products",
            sort_order=1,
            props={"page_size": 12},
        )

    def test_get_published_layout(self):
        response = self.client.get("/api/v1/page-config/home")
        assert response.status_code == 200
        data = response.json()
        assert data["pageType"] == "home"
        assert data["title"] == "Homepage"
        assert data["isPublished"] is True
        assert len(data["sections"]) == 2

    def test_sections_include_props(self):
        response = self.client.get("/api/v1/page-config/home")
        sections = response.json()["sections"]
        products = next(
            s for s in sections if s["componentType"] == "products_grid"
        )
        assert products["props"] == {"pageSize": 12}

    def test_404_for_unpublished(self):
        PageLayout.objects.create(
            page_type="draft",
            title="Draft",
            is_published=False,
        )
        response = self.client.get("/api/v1/page-config/draft")
        assert response.status_code == 404

    def test_404_for_nonexistent(self):
        response = self.client.get("/api/v1/page-config/nonexistent")
        assert response.status_code == 404


class TestPageLayoutAdminViewSet(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=self.admin)

        # Page-layout admin is ``IsPlatformSuperuser``. It previously
        # paired ``IsAdminUser`` with ``HasTenantAccess``, but that
        # membership lookup compared
        # primary keys ACROSS schemas on an API request — see
        # ``docs/api-staff-identity.md``. A store operator administering
        # their store through the API is deliberately NOT supported yet;
        # they use the Django admin, where role-derived permissions
        # apply.
        #
        # The tenant + membership below are kept because the layouts
        # themselves are tenant-scoped data.
        self.tenant = Tenant(
            schema_name="page_admin_test",
            name="Page Admin Test",
            slug="page-admin-test",
            owner_email="owner-page-admin@example.com",
        )
        self.tenant.auto_create_schema = False
        self.tenant.save()
        UserTenantMembership.objects.create(
            user=self.admin,
            tenant=self.tenant,
            role=TenantMembershipRole.ADMIN,
            is_active=True,
        )

        # Bind the tenant to the active connection so
        # ``HasTenantAccess`` sees it via ``get_current_tenant``. We
        # restore the previous value in ``tearDown`` so other tests
        # in the same module aren't affected.
        self._previous_tenant = getattr(connection, "tenant", None)
        connection.tenant = self.tenant

    def tearDown(self):
        try:
            connection.tenant = self._previous_tenant
        except AttributeError:
            pass

    def test_list(self):
        PageLayout.objects.create(page_type="home", title="Homepage")
        response = self.client.get("/api/v1/page-config/admin")
        assert response.status_code == 200

    def test_a_store_operator_is_refused(self):
        """An unstamped operator is refused even with an ADMIN membership.

        This identity is a tenant-schema session: it carries no platform
        provenance stamp, so its membership must not be matched by pk
        across schemas. Only ``StaffBearer`` tokens and platform-staff
        sessions (``docs/api-staff-identity.md``) reach the store-scoped
        administrative routes.
        """
        operator = UserAccountFactory(is_staff=True, is_superuser=False)
        UserTenantMembership.objects.create(
            user=operator,
            tenant=self.tenant,
            role=TenantMembershipRole.ADMIN,
            is_active=True,
        )
        client = APIClient()
        client.force_authenticate(user=operator)
        response = client.get("/api/v1/page-config/admin")
        assert response.status_code == 403

    def test_create_with_sections(self):
        data = {
            "pageType": "home",
            "title": "Homepage",
            "isPublished": True,
            "sections": [
                {
                    "componentType": "hero_carousel",
                    "title": "Carousel",
                    "isVisible": True,
                    "props": {},
                },
                {
                    "componentType": "products_grid",
                    "title": "Products",
                    "isVisible": True,
                    "props": {"pageSize": 12},
                },
            ],
        }
        response = self.client.post(
            "/api/v1/page-config/admin",
            data=data,
            format="json",
        )
        assert response.status_code == 201
        result = response.json()
        assert result["pageType"] == "home"
        assert len(result["sections"]) == 2

    def test_update_replaces_sections(self):
        layout = PageLayout.objects.create(page_type="home", title="Homepage")
        PageSection.objects.create(
            layout=layout,
            component_type=ComponentType.HERO_CAROUSEL,
            sort_order=0,
        )
        data = {
            "pageType": "home",
            "title": "Homepage Updated",
            "sections": [
                {
                    "componentType": "spacer",
                    "title": "",
                    "isVisible": True,
                    "props": {"height": "lg"},
                },
            ],
        }
        response = self.client.put(
            f"/api/v1/page-config/admin/{layout.pk}",
            data=data,
            format="json",
        )
        assert response.status_code == 200
        result = response.json()
        assert result["title"] == "Homepage Updated"
        assert len(result["sections"]) == 1
        assert result["sections"][0]["componentType"] == "spacer"

    def test_partial_update_preserves_sections(self):
        layout = PageLayout.objects.create(page_type="home", title="Homepage")
        PageSection.objects.create(
            layout=layout,
            component_type=ComponentType.HERO_CAROUSEL,
            sort_order=0,
        )
        data = {"title": "Homepage Patched"}
        response = self.client.patch(
            f"/api/v1/page-config/admin/{layout.pk}",
            data=data,
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Homepage Patched"
        assert layout.sections.count() == 1

    def test_unauthenticated_denied(self):
        self.client.logout()
        response = self.client.get("/api/v1/page-config/admin")
        assert response.status_code in (401, 403)

    def test_non_staff_denied(self):
        regular_user = UserAccountFactory(is_staff=False)
        self.client.force_authenticate(user=regular_user)
        response = self.client.get("/api/v1/page-config/admin")
        assert response.status_code == 403


class TestContentPageViewSet(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.published = _make_content_page(
            "terms", "Όροι Χρήσης", is_published=True
        )
        self.draft = _make_content_page(
            "privacy", "Πολιτική Απορρήτου", is_published=False
        )

    def test_anonymous_get_published_by_slug(self):
        response = self.client.get("/api/v1/content-page/terms")
        assert response.status_code == 200
        assert response.json()["slug"] == "terms"

    def test_anonymous_unpublished_returns_404(self):
        response = self.client.get("/api/v1/content-page/privacy")
        assert response.status_code == 404

    def test_anonymous_nonexistent_slug_returns_404(self):
        response = self.client.get("/api/v1/content-page/does-not-exist")
        assert response.status_code == 404

    def test_list_returns_only_published_for_anonymous(self):
        response = self.client.get("/api/v1/content-page")
        assert response.status_code == 200
        slugs = [item["slug"] for item in response.data["results"]]
        assert "terms" in slugs
        assert "privacy" not in slugs

    def test_staff_sees_unpublished_in_list(self):
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get("/api/v1/content-page")
        assert response.status_code == 200
        slugs = [item["slug"] for item in response.data["results"]]
        assert "privacy" in slugs

    def test_staff_can_retrieve_unpublished_by_slug(self):
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get("/api/v1/content-page/privacy")
        assert response.status_code == 200

    def test_anonymous_create_denied(self):
        response = self.client.post(
            "/api/v1/content-page",
            data={
                "slug": "faq",
                "isPublished": False,
                "translations": {
                    "el": {"title": "Συχνές Ερωτήσεις", "body": ""}
                },
            },
            format="json",
        )
        assert response.status_code in (401, 403)

    def test_non_staff_create_denied(self):
        regular_user = UserAccountFactory(is_staff=False)
        self.client.force_authenticate(user=regular_user)
        response = self.client.post(
            "/api/v1/content-page",
            data={
                "slug": "faq",
                "isPublished": False,
                "translations": {
                    "el": {"title": "Συχνές Ερωτήσεις", "body": ""}
                },
            },
            format="json",
        )
        assert response.status_code == 403

    def test_staff_can_create(self):
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=staff)
        response = self.client.post(
            "/api/v1/content-page",
            data={
                "slug": "faq",
                "isPublished": False,
                "translations": {
                    "el": {"title": "Συχνές Ερωτήσεις", "body": ""}
                },
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["slug"] == "faq"
