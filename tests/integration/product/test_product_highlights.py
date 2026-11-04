import json

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation
from rest_framework import status

from admin.base import BaseTranslatableTabularInline
from core.api.serializers import ContentObjectRelatedField
from product.admin import ProductAdmin, ProductHighlightInline
from product.factories.highlight import ProductHighlightFactory
from product.factories.product import ProductFactory
from product.models import Product, ProductHighlight
from product.serializers.product import (
    ProductDetailSerializer,
    ProductFeedSerializer,
    ProductSerializer,
)
from tests.utils.store_settings import switch_every_feature_on
from user.factories import UserAccountFactory


def _highlight(product, **texts_by_language):
    """A highlight whose only translations are the ones given."""
    highlight = ProductHighlight.objects.create(product=product)
    for language, text in texts_by_language.items():
        highlight.set_current_language(language)
        highlight.text = text
        highlight.save()
    return highlight


def _feed(query=None, **headers):
    response = Client().get(reverse("product-feed"), query, **headers)
    assert response.status_code == status.HTTP_200_OK
    return json.loads(response.content)["results"]


def _feed_highlights(language, product):
    # By id: the feed paginates, and other tests' products may fill a page.
    [row] = _feed({"id": product.pk}, headers={"X-Language": language})
    return row["highlights"]


@pytest.fixture
def product(db):
    return ProductFactory(active=True, stock=5, num_images=0, num_reviews=0)


@pytest.mark.django_db
class TestHighlightModel:
    def test_a_new_highlight_goes_last(self, product):
        first = _highlight(product, el="Πρώτο")
        second = _highlight(product, el="Δεύτερο")
        third = _highlight(product, el="Τρίτο")

        assert [first.sort_order, second.sort_order, third.sort_order] == [
            0,
            1,
            2,
        ]
        assert list(product.highlights.all()) == [first, second, third]

    def test_the_limit_is_googles_150_characters(self, product):
        highlight = _highlight(product, el="x")
        translation = highlight.get_translation("el")

        translation.text = "x" * 150
        translation.full_clean()

        translation.text = "x" * 151
        with pytest.raises(ValidationError) as raised:
            translation.full_clean()
        assert "text" in raised.value.message_dict

    def test_a_translation_needs_its_text(self, product):
        translation = _highlight(product, el="x").get_translation("el")

        translation.text = ""
        with pytest.raises(ValidationError) as raised:
            translation.full_clean()
        assert "text" in raised.value.message_dict

    def test_erasing_the_product_for_good_erases_its_highlights(self, product):
        _highlight(product, el="Ένα")

        Product._base_manager.filter(pk=product.pk).delete()

        assert not ProductHighlight.objects.exists()


@pytest.mark.django_db
class TestFeedHighlights:
    def test_the_feed_row_carries_the_texts_in_the_merchants_order(
        self, product
    ):
        second = _highlight(product, el="Δεύτερο")
        _highlight(product, el="Τρίτο")
        first = _highlight(product, el="Πρώτο")
        ProductHighlight.objects.filter(pk=first.pk).update(sort_order=-1)
        ProductHighlight.objects.filter(pk=second.pk).update(sort_order=0)

        assert _feed_highlights("el", product) == ["Πρώτο", "Δεύτερο", "Τρίτο"]

    def test_a_product_without_highlights_has_an_empty_list(self, product):
        assert _feed_highlights("el", product) == []

    def test_each_language_gets_only_its_own_texts(self, product):
        _highlight(product, el="Ελληνικό", en="English")

        assert _feed_highlights("el", product) == ["Ελληνικό"]
        assert _feed_highlights("en", product) == ["English"]

    def test_a_highlight_missing_in_the_language_is_left_out(self, product):
        _highlight(product, el="Μόνο ελληνικά")
        _highlight(product, el="Και τα δύο", en="Both")

        assert _feed_highlights("en", product) == ["Both"]
        assert _feed_highlights("el", product) == [
            "Μόνο ελληνικά",
            "Και τα δύο",
        ]

    def test_the_queries_do_not_grow_with_the_products(self, db):
        def queries():
            with CaptureQueriesContext(connection) as captured:
                _feed(headers={"X-Language": "en"})
            return len(captured)

        one = ProductFactory(active=True, stock=5, num_images=0, num_reviews=0)
        _highlight(one, el="Ένα", en="One")
        _highlight(one, el="Δύο")
        with_one_product = queries()

        for _ in range(4):
            other = ProductFactory(
                active=True, stock=5, num_images=0, num_reviews=0
            )
            _highlight(other, el="Ένα", en="One")
            _highlight(other, el="Δύο")
            _highlight(other, el="Τρία", en="Three")

        assert queries() == with_one_product


@pytest.mark.django_db
class TestOnlyTheFeedCarriesHighlights:
    def test_the_feed_serializer_adds_exactly_the_highlights(self):
        assert set(ProductFeedSerializer.Meta.fields) - set(
            ProductSerializer.Meta.fields
        ) == {"highlights"}

    def test_the_shared_serializers_do_not_gain_the_field(self):
        assert "highlights" not in ProductSerializer.Meta.fields
        assert "highlights" not in ProductDetailSerializer.Meta.fields

    def test_the_product_detail_response_has_no_highlights(self, product):
        _highlight(product, el="Ένα")

        response = Client().get(reverse("product-detail", args=[product.pk]))

        assert response.status_code == status.HTTP_200_OK
        assert "highlights" not in response.json()

    def test_the_product_list_has_no_highlights(self, product):
        _highlight(product, el="Ένα")

        response = Client().get(reverse("product-list"))

        assert response.status_code == status.HTTP_200_OK
        assert "highlights" not in response.json()["results"][0]

    def test_a_hydrated_recommendation_has_no_highlights(self, product):
        _highlight(product, el="Ένα")

        data = ContentObjectRelatedField(read_only=True).to_representation(
            Product.objects.for_full_list().get(pk=product.pk)
        )

        assert "highlights" not in data


@pytest.mark.django_db
class TestHighlightsInAdmin:
    def test_the_inline_is_a_sortable_translated_tab_of_the_product(self):
        inline = ProductHighlightInline(Product, AdminSite())

        assert issubclass(inline.__class__, BaseTranslatableTabularInline)
        assert ProductHighlightInline in ProductAdmin.inlines
        assert inline.fields == ("text", "sort_order")
        assert inline.ordering_field == "sort_order"
        assert inline.tab

    def test_the_field_tells_the_merchant_googles_rules(self):
        field = ProductHighlight._parler_meta.get_model_by_field(
            "text"
        )._meta.get_field("text")
        with translation.override("en"):
            text = str(field.help_text)

        assert "at least 2" in text
        assert "4 to 6" in text

    def test_the_product_page_shows_the_highlights_of_the_language_tab(self):
        switch_every_feature_on()
        client = Client()
        client.force_login(
            UserAccountFactory(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )
        product = ProductFactory(num_images=0, num_reviews=0)
        _highlight(product, el="Φιλικό στο δέρμα", en="Gentle on the skin")
        url = reverse("admin:product_product_change", args=[product.pk])

        el = client.get(url, {"language": "el"})
        en = client.get(url, {"language": "en"})

        assert el.status_code == en.status_code == status.HTTP_200_OK
        assert "Φιλικό στο δέρμα" in el.content.decode()
        assert "Gentle on the skin" not in el.content.decode()
        assert "Gentle on the skin" in en.content.decode()

    def test_saving_the_inline_stores_the_text_in_that_language(self):
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(is_superuser=True, is_staff=True)
        product = ProductFactory(num_images=0, num_reviews=0)
        highlight = _highlight(product, el="Ελληνικό")
        inline = ProductHighlightInline(Product, AdminSite())
        formset_class = inline.get_formset(request, product)
        formset_class.language_code = "en"

        formset = formset_class(
            data={
                "highlights-TOTAL_FORMS": "2",
                "highlights-INITIAL_FORMS": "1",
                "highlights-MIN_NUM_FORMS": "0",
                "highlights-MAX_NUM_FORMS": "1000",
                "highlights-0-id": str(highlight.pk),
                "highlights-0-product": str(product.pk),
                "highlights-0-text": "English",
                "highlights-0-sort_order": "0",
                "highlights-1-product": str(product.pk),
                "highlights-1-text": "Second",
                "highlights-1-sort_order": "1",
            },
            instance=product,
            prefix="highlights",
        )
        assert formset.is_valid(), formset.errors
        formset.save()

        stored = ProductHighlight.objects.get(pk=highlight.pk)
        assert stored.get_translation("el").text == "Ελληνικό"
        assert stored.get_translation("en").text == "English"
        assert product.highlights.count() == 2
        assert _feed_highlights("en", product) == ["English", "Second"]

    def test_the_inline_refuses_text_over_the_limit(self):
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(is_superuser=True, is_staff=True)
        product = ProductFactory(num_images=0, num_reviews=0)
        inline = ProductHighlightInline(Product, AdminSite())
        formset_class = inline.get_formset(request, product)
        formset_class.language_code = "el"

        formset = formset_class(
            data={
                "highlights-TOTAL_FORMS": "1",
                "highlights-INITIAL_FORMS": "0",
                "highlights-MIN_NUM_FORMS": "0",
                "highlights-MAX_NUM_FORMS": "1000",
                "highlights-0-product": str(product.pk),
                "highlights-0-text": "x" * 151,
                "highlights-0-sort_order": "0",
            },
            instance=product,
            prefix="highlights",
        )

        assert not formset.is_valid()


def test_the_factory_builds_a_highlight_in_every_language(db):
    highlight = ProductHighlightFactory()

    assert highlight.get_available_languages()
