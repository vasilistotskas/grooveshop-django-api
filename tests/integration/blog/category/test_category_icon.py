"""A blog category's icon: one of the icons the store's catalogue offers.

The list is the storefront's (the catalogue's ``icons``), so every check
here goes through the faked catalogue endpoint, never a copy of the names.
"""

from __future__ import annotations

from unittest import mock

import requests
from django.contrib.admin.sites import site
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import translation
from rest_framework import status
from rest_framework.test import APITestCase

from blog.admin import BlogCategoryAdmin
from blog.factories.category import BlogCategoryFactory
from blog.models.category import BlogCategory
from builder.storefront import clear_catalogue_cache
from tests.utils import TestURLFixerMixin
from tests.utils.builder import FakeCatalogue
from user.factories.account import UserAccountFactory

ROBOT = "i-fa6-solid-robot"


@override_settings(NUXT_INTERNAL_BASE_URL="http://storefront.test")
class BlogCategoryIconTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.fake = FakeCatalogue()
        clear_catalogue_cache()
        patcher = mock.patch(
            "builder.storefront.requests.get", side_effect=self.fake.get
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(clear_catalogue_cache)
        user = UserAccountFactory(
            num_addresses=0, is_superuser=True, is_staff=True
        )
        self.client.force_authenticate(user=user)
        self.category = BlogCategoryFactory(slug="ai")

    def patch(self, **body):
        return self.client.patch(
            reverse("blog-category-detail", args=[self.category.pk]),
            body,
            format="json",
        )

    def storefront_down(self):
        clear_catalogue_cache()
        self.fake.failure = requests.ConnectionError("down")

    def test_an_offered_icon_is_saved_and_served(self):
        response = self.patch(icon=ROBOT)

        assert response.status_code == status.HTTP_200_OK, response.data
        self.category.refresh_from_db()
        assert self.category.icon == ROBOT
        listed = self.client.get(reverse("blog-category-list"))
        [row] = [
            r for r in listed.data["results"] if r["id"] == self.category.pk
        ]
        assert row["icon"] == ROBOT

    def test_a_category_is_created_with_an_offered_icon(self):
        response = self.client.post(
            reverse("blog-category-list"),
            {
                "translations": {"el": {"name": "Ρομπότ"}},
                "slug": "robots",
                "icon": ROBOT,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert BlogCategory.objects.get(slug="robots").icon == ROBOT

    def test_an_icon_the_store_does_not_offer_is_refused(self):
        response = self.patch(icon="i-mdi-gamepad")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["fields"]["icon"][0]["code"] == "unknown_icon"
        self.category.refresh_from_db()
        assert self.category.icon == ""

    def test_a_changed_icon_is_refused_when_the_storefront_does_not_answer(
        self,
    ):
        self.storefront_down()

        response = self.patch(icon=ROBOT)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.data["code"] == "storefront_unavailable"

    def test_an_unchanged_icon_is_not_checked_again(self):
        # Saved while the builder offered it; the storefront is down now.
        self.category.icon = "i-retired-icon"
        self.category.save(update_fields=["icon"])
        self.storefront_down()

        response = self.patch(icon="i-retired-icon", slug="ai-renamed")

        assert response.status_code == status.HTTP_200_OK, response.data
        self.category.refresh_from_db()
        assert self.category.slug == "ai-renamed"

    def test_clearing_the_icon_needs_no_catalogue(self):
        self.category.icon = ROBOT
        self.category.save(update_fields=["icon"])
        self.storefront_down()

        response = self.patch(icon="")

        assert response.status_code == status.HTTP_200_OK, response.data
        self.category.refresh_from_db()
        assert self.category.icon == ""

    def test_a_new_category_has_no_icon(self):
        assert BlogCategoryFactory(slug="web").icon == ""


@override_settings(NUXT_INTERNAL_BASE_URL="http://storefront.test")
class BlogCategoryAdminFormIconTestCase(APITestCase):
    def setUp(self):
        self.fake = FakeCatalogue()
        clear_catalogue_cache()
        patcher = mock.patch(
            "builder.storefront.requests.get", side_effect=self.fake.get
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(clear_catalogue_cache)

    def form(self, instance=None, data=None):
        """The category form as the admin builds it (``get_form``),
        submitted when *data* is given."""
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(
            num_addresses=0, is_superuser=True, is_staff=True
        )
        model_admin = BlogCategoryAdmin(BlogCategory, site)
        form_class = model_admin.get_form(request, instance)
        return form_class(data=data, instance=instance)

    def submit(self, category, **changes):
        """The change form of *category* posted with *changes*: what an
        editor sends after opening it."""
        data = {
            "name": "AI",
            "description": "",
            "parent": "",
            "slug": category.slug,
            "sort_order": category.sort_order or 0,
            "icon": category.icon,
            **changes,
        }
        return self.form(category, data)

    def choices(self, instance=None):
        return list(self.form(instance).fields["icon"].choices)

    def test_offers_the_catalogue_icons_by_their_name_in_the_admin_language(
        self,
    ):
        with translation.override("el"):
            greek = self.choices()
        with translation.override("en"):
            english = self.choices()

        assert (ROBOT, "Ρομπότ") in greek
        assert (ROBOT, "Robot") in english
        assert greek[0][0] == ""

    def test_labels_a_region_variant_in_its_language(self):
        with translation.override("en-us"):
            choices = self.choices()

        assert (ROBOT, "Robot") in choices

    @override_settings(LANGUAGE_CODE="el")
    def test_labels_in_the_store_language_with_no_active_language(self):
        with translation.override(None):
            choices = self.choices()

        assert (ROBOT, "Ρομπότ") in choices

    def test_keeps_a_saved_icon_the_builder_no_longer_offers(self):
        category = BlogCategoryFactory(slug="old", icon="i-retired-icon")

        assert ("i-retired-icon", "i-retired-icon") in self.choices(category)

    def test_shows_the_saved_icon_unchangeable_when_the_storefront_is_down(
        self,
    ):
        category = BlogCategoryFactory(slug="ai", icon=ROBOT)
        self.fake.failure = requests.ConnectionError("down")

        field = self.form(category).fields["icon"]

        assert field.disabled
        assert list(field.choices) == [(ROBOT, ROBOT)]

    def test_a_submitted_form_saves_an_offered_icon(self):
        category = BlogCategoryFactory(slug="ai")

        form = self.submit(category, icon=ROBOT)

        assert form.is_valid(), form.errors
        form.save()
        category.refresh_from_db()
        assert category.icon == ROBOT

    def test_a_submitted_form_refuses_an_icon_the_store_does_not_offer(self):
        category = BlogCategoryFactory(slug="ai")

        form = self.submit(category, icon="i-mdi-gamepad")

        assert not form.is_valid()
        assert form.errors.as_data()["icon"][0].code == "unknown_icon"

    def test_with_no_catalogue_a_submitted_form_keeps_the_icon_and_saves(
        self,
    ):
        category = BlogCategoryFactory(slug="ai", icon=ROBOT)
        self.fake.failure = requests.ConnectionError("down")

        form = self.submit(category, slug="ai-renamed")

        assert form.is_valid(), form.errors
        form.save()
        category.refresh_from_db()
        assert category.slug == "ai-renamed"
        assert category.icon == ROBOT

    def test_with_no_catalogue_a_changed_icon_is_refused_not_dropped(self):
        # Opened while the catalogue answered, posted once it does not.
        category = BlogCategoryFactory(slug="ai")
        self.fake.failure = requests.ConnectionError("down")

        form = self.submit(category, icon=ROBOT)

        assert not form.is_valid()
        assert form.errors.as_data()["icon"][0].code == "storefront_unavailable"
        category.refresh_from_db()
        assert category.icon == ""
