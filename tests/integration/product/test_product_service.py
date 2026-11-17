"""The catalogue rules the product admin's actions call.

The discount actions save each product, never ``queryset.update()``: a bulk
UPDATE emits no ``post_save``, so no history row, no price-drop alert and no
reindex follow it (``docs/product-pricing.md``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from product import services
from product.factories.product import ProductFactory
from product.models.product import Product
from product.services import ProductDuplicationError, ProductService

pytestmark = pytest.mark.django_db


def _product(**kwargs):
    return ProductFactory(num_images=0, num_reviews=0, **kwargs)


class TestDiscount:
    def test_set_discount_saves_each_product_through_save(self):
        first = _product(discount_percent=Decimal(0))
        second = _product(discount_percent=Decimal(0))
        bystander = _product(discount_percent=Decimal(0))
        saved = []

        with patch.object(
            Product,
            "save",
            autospec=True,
            side_effect=lambda self, **kw: saved.append(
                (self.pk, kw["update_fields"])
            ),
        ):
            count = ProductService.set_discount(
                Product.objects.filter(pk__in=[second.pk, first.pk]),
                Decimal(25),
            )

        assert count == 2
        assert saved == [
            (first.pk, ["discount_percent"]),
            (second.pk, ["discount_percent"]),
        ], "saved in pk order, the discount field alone"
        bystander.refresh_from_db()
        assert bystander.discount_percent == Decimal(0)

    def test_set_discount_persists_the_percent(self):
        product = _product(discount_percent=Decimal(0))

        ProductService.set_discount(
            Product.objects.filter(pk=product.pk), Decimal("12.50")
        )

        product.refresh_from_db()
        assert product.discount_percent == Decimal("12.50")

    def test_clear_discount_resets_to_zero(self):
        product = _product(discount_percent=Decimal(40))

        count = ProductService.clear_discount(
            Product.objects.filter(pk=product.pk)
        )

        assert count == 1
        product.refresh_from_db()
        assert product.discount_percent == Decimal(0)


class TestDuplicateAsDraft:
    def test_the_copy_is_an_inactive_skeleton_with_its_own_identity(self):
        source = _product(
            active=True,
            stock=7,
            view_count=3,
            external_reference="woo:5",
            sku="SKU-1",
            low_stock_alert_sent=True,
        )

        clone = ProductService.duplicate_as_draft(source)

        assert clone.pk != source.pk
        assert clone.uuid != source.uuid
        assert clone.active is False
        assert clone.stock == 0
        assert clone.view_count == 0
        assert clone.low_stock_alert_sent is False
        assert clone.external_reference is None
        assert clone.slug == f"{source.slug}-copy-1"
        assert clone.sku == "SKU-1-COPY-1"
        assert Product.objects.filter(pk=source.pk, active=True).exists()

    def test_a_taken_suffix_is_skipped(self):
        source = _product(sku="SKU-2")

        first = ProductService.duplicate_as_draft(source)
        second = ProductService.duplicate_as_draft(source)

        assert first.slug == f"{source.slug}-copy-1"
        assert second.slug == f"{source.slug}-copy-2"
        assert second.sku == "SKU-2-COPY-2"

    def test_it_gives_up_when_no_suffix_is_free(self):
        source = _product()
        ProductService.duplicate_as_draft(source)

        with (
            patch.object(services, "MAX_COPY_SUFFIX", 1),
            pytest.raises(ProductDuplicationError),
        ):
            ProductService.duplicate_as_draft(source)
        assert Product.objects.count() == 2
