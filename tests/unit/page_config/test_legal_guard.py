"""A store's legal pages stay servable (``page_config.legal_guard``).

The storefront 404s an unpublished or empty legal page, and checkout
refuses every order and card payment while the terms of use or privacy
policy cannot be shown. So the admin and the staff API refuse the edit
that would get there: unpublishing, scheduling for later, renaming,
emptying a served language, deleting. Rewriting the text stays free.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from page_config.admin import ContentPageAdmin
from page_config.models import ContentPage, ContentPageTranslation
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def greek_and_english_store(bind_tenant):
    tenant = store_tenant(
        "legal_guard", default_locale="el", available_locales=["el", "en"]
    )
    bind_tenant(tenant)
    return tenant


def _page(slug: str = "terms", **fields) -> ContentPage:
    page = ContentPage.objects.create(
        slug=slug, is_published=fields.pop("is_published", True), **fields
    )
    for language in ("el", "en"):
        ContentPageTranslation.objects.create(
            master=page,
            language_code=language,
            title=f"{slug} {language}",
            body=f"<p>{slug} {language}</p>",
        )
    return page


def _codes(raised: pytest.ExceptionInfo[ValidationError]) -> set[str | None]:
    return {
        error.code
        for errors in raised.value.error_dict.values()
        for error in errors
    }


class TestModelRules:
    def test_a_legal_page_cannot_be_unpublished(self, greek_and_english_store):
        page = _page()
        page.is_published = False

        with pytest.raises(ValidationError) as raised:
            page.full_clean()

        assert "is_published" in raised.value.error_dict
        assert _codes(raised) == {"legal_page_unpublished"}

    def test_a_legal_page_cannot_be_scheduled_for_later(
        self, greek_and_english_store
    ):
        page = _page()
        page.published_at = timezone.now() + timedelta(days=1)

        with pytest.raises(ValidationError) as raised:
            page.full_clean()

        assert "published_at" in raised.value.error_dict

    def test_a_legal_page_keeps_its_slug(self, greek_and_english_store):
        page = _page()
        page.slug = "old-terms"

        with pytest.raises(ValidationError) as raised:
            page.full_clean()

        assert _codes(raised) == {"legal_page_renamed"}

    def test_another_page_can_be_unpublished(self, greek_and_english_store):
        page = _page("faq")
        page.is_published = False

        page.full_clean()

    def test_a_served_language_keeps_a_body(self, greek_and_english_store):
        translation = _page().translations.get(language_code="en")
        translation.body = "  "

        with pytest.raises(ValidationError) as raised:
            translation.full_clean()

        assert _codes(raised) == {"legal_page_empty"}

    def test_a_language_the_store_does_not_serve_may_be_empty(
        self, bind_tenant
    ):
        bind_tenant(store_tenant("greek_only", default_locale="el"))
        translation = _page().translations.get(language_code="en")
        translation.body = ""

        translation.full_clean()

    def test_the_text_can_be_rewritten(self, greek_and_english_store):
        translation = _page().translations.get(language_code="el")
        translation.body = "<p>Νέοι όροι</p>"

        translation.full_clean()


class TestAdmin:
    def _admin(self):
        return ContentPageAdmin(ContentPage, AdminSite())

    def _request(self):
        request = RequestFactory().post("/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        return request

    def test_a_legal_page_cannot_be_deleted(self, greek_and_english_store):
        assert not self._admin().has_delete_permission(self._request(), _page())

    def test_a_served_translation_cannot_be_deleted(
        self, greek_and_english_store
    ):
        translation = _page().translations.get(language_code="en")

        assert not self._admin().has_delete_permission(
            self._request(), translation
        )

    def test_another_page_can_be_deleted(self, greek_and_english_store):
        assert self._admin().has_delete_permission(
            self._request(), _page("faq")
        )

    def test_the_bulk_delete_keeps_the_legal_pages(
        self, greek_and_english_store
    ):
        _page("terms")
        _page("faq")
        request = self._request()
        request._messages = _Messages()

        self._admin().delete_queryset(request, ContentPage.objects.all())

        assert list(ContentPage.objects.values_list("slug", flat=True)) == [
            "terms"
        ]
        assert request._messages.added


class _Messages:
    """A message store that records what the admin said."""

    def __init__(self):
        self.added = []

    def add(self, level, message, extra_tags=""):
        self.added.append((level, str(message)))


class TestStaffApi:
    @pytest.fixture
    def client(self, greek_and_english_store):
        client = APIClient()
        client.force_authenticate(
            user=UserAccountFactory(is_staff=True, is_superuser=True)
        )
        return client

    def test_unpublishing_a_legal_page_is_refused(self, client):
        _page()

        response = client.patch(
            "/api/v1/content-page/terms",
            {"isPublished": False},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["fields"]["isPublished"][0]["code"] == (
            "legal_page_unpublished"
        )
        assert ContentPage.objects.get(slug="terms").is_published

    def test_emptying_a_served_language_is_refused(self, client):
        _page()

        response = client.patch(
            "/api/v1/content-page/terms",
            {
                "translations": {
                    "el": {"title": "Όροι", "body": "<p>Όροι</p>"},
                    "en": {"title": "Terms", "body": ""},
                }
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["code"] == "legal_page_empty"

    def test_deleting_a_legal_page_is_refused(self, client):
        _page()

        response = client.delete("/api/v1/content-page/terms")

        assert response.status_code == 400
        assert response.json()["code"] == "legal_page_not_deletable"
        assert ContentPage.objects.filter(slug="terms").exists()

    def test_the_text_can_be_rewritten(self, client):
        _page()

        response = client.patch(
            "/api/v1/content-page/terms",
            {"translations": {"el": {"title": "Όροι", "body": "<p>Νέοι</p>"}}},
            format="json",
        )

        assert response.status_code == 200, response.json()

    def test_another_page_can_be_deleted(self, client):
        _page("faq")

        response = client.delete("/api/v1/content-page/faq")

        assert response.status_code == 204

    def test_a_new_served_translation_without_a_body_is_refused(self, client):
        page = _page()
        page.translations.filter(language_code="en").delete()

        response = client.patch(
            "/api/v1/content-page/terms",
            {
                "translations": {
                    "el": {"title": "Όροι"},
                    "en": {"title": "Terms"},
                }
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["code"] == "legal_page_empty"

    def test_a_title_only_edit_keeps_the_stored_body(self, client):
        _page()

        response = client.patch(
            "/api/v1/content-page/terms",
            {
                "translations": {
                    "el": {"title": "Όροι"},
                    "en": {"title": "Terms"},
                }
            },
            format="json",
        )

        assert response.status_code == 200, response.json()

    def test_a_new_legal_page_is_written_in_every_served_language(self, client):
        response = client.post(
            "/api/v1/content-page",
            {
                "slug": "terms",
                "isPublished": True,
                "translations": {
                    "el": {"title": "Όροι", "body": "<p>Όροι</p>"}
                },
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["code"] == "legal_page_empty"
        assert not ContentPage.objects.filter(slug="terms").exists()
