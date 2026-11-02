import pytest
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from page_config.models import ContentPage

pytestmark = pytest.mark.assert_english


class TestContentPage(TestCase):
    def test_create_defaults(self):
        page = ContentPage.objects.create(slug="terms")
        assert page.slug == "terms"
        assert page.is_published is False
        assert page.uuid is not None

    def test_slug_is_unique(self):
        ContentPage.objects.create(slug="terms")
        with self.assertRaises(IntegrityError):
            ContentPage.objects.create(slug="terms")

    def test_translations(self):
        page = ContentPage.objects.create(slug="privacy")
        page.set_current_language("el")
        page.title = "Πολιτική Απορρήτου"
        page.body = "<p>Περιεχόμενο</p>"
        page.save()

        fetched = ContentPage.objects.get(slug="privacy")
        assert (
            fetched.safe_translation_getter("title", any_language=True)
            == "Πολιτική Απορρήτου"
        )
        assert (
            fetched.safe_translation_getter("body", any_language=True)
            == "<p>Περιεχόμενο</p>"
        )

    def test_str_falls_back_to_slug_without_translation(self):
        page = ContentPage.objects.create(slug="faq")
        assert str(page) == "faq"

    def test_str_uses_title_when_translated(self):
        page = ContentPage.objects.create(slug="about")
        page.set_current_language("el")
        page.title = "Σχετικά με εμάς"
        page.save()

        fetched = ContentPage.objects.get(slug="about")
        assert str(fetched) == "Σχετικά με εμάς"

    def test_published_manager(self):
        published = ContentPage.objects.create(
            slug="shipping-info",
            is_published=True,
            published_at=timezone.now(),
        )
        ContentPage.objects.create(slug="return-policy", is_published=False)

        qs = ContentPage.objects.published()
        assert published in qs
        assert qs.count() == 1

    def test_published_manager_includes_null_published_at(self):
        # Mirrors core.models.PublishedQuerySet.published(): an
        # explicit null published_at (bypassing save()'s auto-stamp,
        # e.g. a bulk .update()) must still count as public.
        page = ContentPage.objects.create(slug="terms", is_published=True)
        ContentPage.objects.filter(pk=page.pk).update(published_at=None)
        assert ContentPage.objects.get(pk=page.pk) in (
            ContentPage.objects.published()
        )
