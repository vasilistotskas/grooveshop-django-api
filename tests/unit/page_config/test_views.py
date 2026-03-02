from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from page_config.models import ComponentType, PageLayout, PageSection
from user.factories.account import UserAccountFactory


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
        self.admin = UserAccountFactory(is_staff=True)
        self.client.force_authenticate(user=self.admin)

    def test_list(self):
        PageLayout.objects.create(page_type="home", title="Homepage")
        response = self.client.get("/api/v1/page-config/admin")
        assert response.status_code == 200

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
