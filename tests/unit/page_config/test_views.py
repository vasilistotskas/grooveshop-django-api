from django.test import TestCase
from rest_framework.test import APIClient

from page_config.models import ContentPage
from user.factories.account import UserAccountFactory


def _make_content_page(slug, title, *, is_published, body="<p>Body</p>"):
    page = ContentPage.objects.create(slug=slug, is_published=is_published)
    page.set_current_language("el")
    page.title = title
    page.body = body
    page.save()
    return page


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

    def test_seo_travels_per_language_in_translations(self):
        self.published.set_current_language("el")
        self.published.seo_title = "Όροι Χρήσης | Κατάστημα"
        self.published.save()
        self.published.set_current_language("en")
        self.published.title = "Terms of Use"
        self.published.seo_title = "Terms of Use | Store"
        self.published.save()

        data = self.client.get("/api/v1/content-page/terms").json()

        assert "seoTitle" not in data
        assert data["translations"]["el"]["seoTitle"] == (
            "Όροι Χρήσης | Κατάστημα"
        )
        assert data["translations"]["en"]["seoTitle"] == "Terms of Use | Store"
        assert data["translations"]["en"]["seoDescription"] == ""

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

    def _terms_in_list(self):
        response = self.client.get("/api/v1/content-page")
        return next(
            item
            for item in response.json()["results"]
            if item["slug"] == "terms"
        )

    def test_list_carries_the_title_and_not_the_document(self):
        self.published.set_current_language("el")
        self.published.seo_title = "Όροι Χρήσης | Κατάστημα"
        self.published.save()

        page = self._terms_in_list()

        assert page["translations"]["el"] == {"title": "Όροι Χρήσης"}

    def test_list_names_the_languages_a_page_is_written_in(self):
        self.published.set_current_language("en")
        self.published.title = "Terms of Use"
        self.published.body = "<p>&nbsp;</p>"
        self.published.save()

        assert self._terms_in_list()["translatedLocales"] == ["el"]

    def test_detail_carries_the_whole_document(self):
        data = self.client.get("/api/v1/content-page/terms").json()

        assert data["translations"]["el"]["body"] == "<p>Body</p>"
        assert data["translatedLocales"] == ["el"]

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
