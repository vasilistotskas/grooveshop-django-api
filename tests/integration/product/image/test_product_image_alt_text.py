import pytest
from django.contrib.admin.sites import AdminSite
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import translation

from admin.base import BaseTranslatableTabularInline
from product.admin import (
    ProductAdmin,
    ProductCategoryImageAdmin,
    ProductImageAdmin,
    ProductImageInline,
)
from product.factories.category_image import ProductCategoryImageFactory
from product.factories.image import ProductImageFactory
from product.models import Product, ProductCategoryImage, ProductImage
from product.serializers.category_image import ProductCategoryImageSerializer
from product.serializers.image import ProductImageSerializer
from tests.utils.store_settings import switch_every_feature_on
from user.factories import UserAccountFactory


def _image(**texts_by_language):
    """An image whose only translations are the ones given."""
    image = ProductImageFactory(translations=[])
    for language, fields in texts_by_language.items():
        image.set_current_language(language)
        for name, value in fields.items():
            setattr(image, name, value)
        image.save()
    return ProductImage.objects.get(pk=image.pk)


def _alt(image, language="el"):
    # parler fixes an instance's language when it is loaded, as a request
    # does under its active language.
    with translation.override(language):
        image = ProductImage.objects.get(pk=image.pk)
        return ProductImageSerializer(image).data["alt_text"]


@pytest.mark.django_db
class TestAltTextPrecedence:
    def test_the_merchants_alt_text_is_served(self):
        image = _image(el={"alt_text": "Κόκκινο παπούτσι"})

        assert _alt(image) == "Κόκκινο παπούτσι"

    def test_an_image_with_no_text_has_an_empty_alt_not_a_sentence(self):
        image = _image(el={"alt_text": ""})

        assert _alt(image) == ""

    def test_an_image_with_no_translation_has_an_empty_alt(self):
        image = _image()

        assert _alt(image) == ""

    def test_the_request_language_is_preferred(self):
        image = _image(el={"alt_text": "Ελληνικό"}, en={"alt_text": "English"})

        assert _alt(image, "en") == "English"
        assert _alt(image, "el") == "Ελληνικό"

    def test_another_language_fills_in_when_the_current_has_none(self):
        image = _image(el={"alt_text": "Ελληνικό"})

        assert _alt(image, "en") == "Ελληνικό"

    def test_the_translations_map_carries_alt_text(self):
        image = _image(el={"alt_text": "Ελληνικό"})

        data = ProductImageSerializer(image).data

        assert data["translations"]["el"]["alt_text"] == "Ελληνικό"


@pytest.mark.django_db
class TestAltTextInAdmin:
    def test_the_product_page_inline_edits_alt_text(self):
        inline = ProductImageInline(Product, AdminSite())

        assert issubclass(inline.__class__, BaseTranslatableTabularInline)
        assert inline.fields == ("image", "is_main", "alt_text")

    def test_the_inline_is_registered_on_the_product_page(self):
        assert ProductImageInline in ProductAdmin.inlines

    def test_the_product_page_renders_the_alt_text_of_the_language_tab(self):
        switch_every_feature_on()
        user = UserAccountFactory(admin=True)
        client = Client()
        client.force_login(
            user, backend="tenant.auth_backends.PlatformStaffBackend"
        )
        image = _image(
            el={"alt_text": "Κόκκινο παπούτσι"}, en={"alt_text": "Red shoe"}
        )
        url = reverse("admin:product_product_change", args=[image.product_id])

        el = client.get(url, {"language": "el"})
        en = client.get(url, {"language": "en"})

        assert el.status_code == en.status_code == 200
        assert "Κόκκινο παπούτσι" in el.content.decode()
        assert "Red shoe" not in el.content.decode()
        assert "Red shoe" in en.content.decode()

    def test_saving_the_inline_stores_the_alt_text_in_that_language(self):
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(is_superuser=True, is_staff=True)
        image = _image(el={"alt_text": "Παπούτσι"})
        inline = ProductImageInline(Product, AdminSite())
        formset_class = inline.get_formset(request, image.product)
        formset_class.language_code = "en"

        formset = formset_class(
            data={
                "images-TOTAL_FORMS": "1",
                "images-INITIAL_FORMS": "1",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "images-0-id": str(image.pk),
                "images-0-product": str(image.product_id),
                "images-0-is_main": "on" if image.is_main else "",
                "images-0-alt_text": "Red shoe",
            },
            instance=image.product,
            prefix="images",
        )
        assert formset.is_valid(), formset.errors
        formset.save()

        stored = ProductImage.objects.get(pk=image.pk)
        stored.set_current_language("en")
        assert stored.alt_text == "Red shoe"
        stored.set_current_language("el")
        assert stored.alt_text == "Παπούτσι"

    def test_the_image_page_edits_alt_text(self):
        fields = [
            field
            for _name, options in ProductImageAdmin.fieldsets
            for field in options["fields"]
        ]

        assert "alt_text" in fields


@pytest.mark.django_db
class TestTitleIsRetired:
    """The ``title`` of an image is shown nowhere; ``alt_text`` is the one text."""

    @pytest.mark.parametrize(
        "model",
        [ProductImage, ProductCategoryImage],
        ids=["product", "category"],
    )
    def test_the_translations_model_has_no_title(self, model):
        names = {
            field.name
            for field in model._parler_meta.root_model._meta.get_fields()
        }

        assert "title" not in names
        assert "alt_text" in names

    def test_the_serialized_translations_carry_no_title(self):
        image = _image(el={"alt_text": "Κόκκινο παπούτσι"})

        data = ProductImageSerializer(image).data

        assert set(data["translations"]["el"]) == {"alt_text"}

    def test_the_category_image_serializer_carries_no_title(self):
        image = ProductCategoryImageFactory()

        data = ProductCategoryImageSerializer(image).data

        assert all("title" not in row for row in data["translations"].values())

    def test_no_admin_page_edits_a_title(self):
        product_fields = [
            field
            for _name, options in ProductImageAdmin.fieldsets
            for field in options["fields"]
        ]
        category_fields = [
            field
            for _name, options in ProductCategoryImageAdmin.fieldsets
            for field in options["fields"]
        ]

        assert "title" not in product_fields + category_fields
        assert "title" not in ProductImageInline.fields

    def test_the_admin_searches_the_alt_text(self):
        assert "translations__alt_text" in ProductImageAdmin.search_fields
        assert "translations__alt_text" in (
            ProductCategoryImageAdmin.search_fields
        )
