"""Renaming what a product's search text is built from reindexes it.

A product's document carries its brand's name, its category's name and
its attributes' names and values. Those rows are saved on their own, so
``Product``'s own receivers never run: before this, the documents kept the
old name until the nightly full sync.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.brand import BrandFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory

TASK = "product.tasks.reindex_products_for_search_text.apply_async"


def online():
    """The engine on, around the one save under test: creating the
    fixtures' rows must not queue documents."""
    return override_settings(
        MEILISEARCH={**settings.MEILISEARCH, "OFFLINE": False}
    )


def dispatched(mock):
    return [call.kwargs["kwargs"] for call in mock.call_args_list]


@pytest.mark.django_db
def test_renaming_a_brand_reindexes_its_products(
    django_capture_on_commit_callbacks,
):
    brand = BrandFactory()
    ProductFactory(brand=brand)

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        brand.name = "Renamed brand"
        brand.save()

    assert dispatched(task) == [{"source": "brand", "pk": brand.pk}]


@pytest.mark.django_db
def test_saving_a_brand_without_renaming_it_does_nothing(
    django_capture_on_commit_callbacks,
):
    brand = BrandFactory()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        brand.save()

    task.assert_not_called()


@pytest.mark.django_db
def test_renaming_a_category_reindexes_its_products(
    django_capture_on_commit_callbacks,
):
    category = ProductCategoryFactory()
    ProductFactory(category=category)
    translation = category.translations.first()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        translation.name = "Renamed category"
        translation.save()

    assert dispatched(task) == [{"source": "category", "pk": category.pk}]


@pytest.mark.django_db
def test_editing_a_category_description_does_not_reindex(
    django_capture_on_commit_callbacks,
):
    category = ProductCategoryFactory()
    translation = category.translations.first()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        translation.description = "<p>New text</p>"
        translation.save()

    task.assert_not_called()


@pytest.mark.django_db
def test_renaming_an_attribute_reindexes_its_products(
    django_capture_on_commit_callbacks,
):
    attribute = AttributeFactory()
    translation = attribute.translations.first()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        translation.name = "Renamed attribute"
        translation.save()

    assert dispatched(task) == [{"source": "attribute", "pk": attribute.pk}]


@pytest.mark.django_db
def test_renaming_an_attribute_value_reindexes_its_products(
    django_capture_on_commit_callbacks,
):
    value = AttributeValueFactory()
    translation = value.translations.first()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        translation.value = "Renamed value"
        translation.save()

    assert dispatched(task) == [{"source": "attribute_value", "pk": value.pk}]


@pytest.mark.django_db
def test_a_new_translation_reindexes_the_products_too(
    django_capture_on_commit_callbacks,
):
    category = ProductCategoryFactory()
    category.translations.all().delete()

    with (
        online(),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        category.create_translation("en", name="Added later")

    assert dispatched(task) == [{"source": "category", "pk": category.pk}]


@pytest.mark.django_db
def test_nothing_is_dispatched_while_the_engine_is_offline(
    django_capture_on_commit_callbacks,
):
    brand = BrandFactory()

    with (
        override_settings(
            MEILISEARCH={**settings.MEILISEARCH, "OFFLINE": True}
        ),
        patch(TASK) as task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        brand.name = "Renamed brand"
        brand.save()

    task.assert_not_called()


@pytest.mark.django_db
def test_the_task_reindexes_exactly_the_products_that_use_the_row():
    from product.models.product_attribute import ProductAttribute
    from product.tasks import reindex_products_for_search_text

    value = AttributeValueFactory()
    brand = BrandFactory()
    category = ProductCategoryFactory()
    branded = ProductFactory(brand=brand, category=category)
    other = {
        "brand": BrandFactory(),
        "category": ProductCategoryFactory(),
    }
    attributed = ProductFactory(**other)
    ProductAttribute.objects.create(product=attributed, attribute_value=value)
    ProductFactory(**other)

    def reindexed(source, pk):
        with patch("product.signals.reindex_products_by_pk") as reindex:
            reindex_products_for_search_text(source, pk)
        return set(reindex.call_args.args[0])

    assert reindexed("brand", brand.pk) == {branded.pk}
    assert reindexed("category", category.pk) == {branded.pk}
    assert reindexed("attribute_value", value.pk) == {attributed.pk}
    assert reindexed("attribute", value.attribute_id) == {attributed.pk}


@pytest.mark.django_db
def test_deleting_a_brand_reindexes_its_products_without_it(
    django_capture_on_commit_callbacks,
):
    brand = BrandFactory()
    branded = ProductFactory(brand=brand)
    ProductFactory()

    with (
        online(),
        patch("meili.tasks.index_document_task.apply_async") as index,
        django_capture_on_commit_callbacks(execute=True),
    ):
        brand.delete()

    dispatched_pks = {
        call.kwargs["kwargs"]["pk"] for call in index.call_args_list
    }
    assert dispatched_pks == set(
        branded.translations.values_list("pk", flat=True)
    )
    branded.refresh_from_db()
    assert branded.brand_id is None
