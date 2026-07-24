from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.category import ProductCategoryFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.factories.variant_group import ProductVariantGroupFactory
from product.models.product_attribute import ProductAttribute
from tests.utils import count_queries
from vat.factories import VatFactory


def _attribute(name, *, is_variant, sort_order=0):
    attribute = AttributeFactory(active=True, is_variant=is_variant)
    attribute.sort_order = sort_order
    attribute.set_current_language("el")
    attribute.name = name
    attribute.save()
    return attribute


def _value(attribute, value, sort_order=0):
    av = AttributeValueFactory(attribute=attribute, active=True)
    av.sort_order = sort_order
    av.set_current_language("el")
    av.value = value
    av.save()
    return av


class ProductVariantsActionTestCase(APITestCase):
    def setUp(self):
        self.category = ProductCategoryFactory()
        self.vat = VatFactory()

        # Variant axis: Colour (is_variant=True) with three values.
        self.colour = _attribute("Χρώμα", is_variant=True, sort_order=0)
        self.white = _value(self.colour, "Λευκό", sort_order=0)
        self.black = _value(self.colour, "Μαύρο", sort_order=1)
        self.blue = _value(self.colour, "Μπλε", sort_order=2)

        # Plain spec attribute (is_variant=False) — must NOT become an axis.
        self.material = _attribute("Υλικό", is_variant=False, sort_order=1)
        self.plastic = _value(self.material, "Πλαστικό")

        self.group = ProductVariantGroupFactory(active=True)

        # Three sibling colour variants in the same group.
        self.products = {}
        for idx, (price, colour_value) in enumerate(
            [
                ("10.00", self.white),
                ("11.00", self.black),
                ("12.00", self.blue),
            ]
        ):
            product = ProductFactory(
                category=self.category,
                vat=self.vat,
                price=price,
                stock=5,
                active=True,
                variant_group=self.group,
                discount_percent=0,
            )
            ProductImageFactory(product=product, is_main=True)
            ProductAttributeFactory(
                product=product, attribute_value=colour_value
            )
            # Every variant also carries the shared spec attribute.
            ProductAttributeFactory(
                product=product, attribute_value=self.plastic
            )
            self.products[colour_value.id] = product

        self.white_product = self.products[self.white.id]

        # A standalone product with no group.
        self.lonely = ProductFactory(
            category=self.category, vat=self.vat, active=True
        )
        ProductImageFactory(product=self.lonely, is_main=True)

    def url(self, pk):
        return reverse("product-variants", args=[pk])

    def test_returns_all_siblings_in_group(self):
        response = self.client.get(self.url(self.white_product.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("axes", response.data)
        self.assertIn("variants", response.data)

        variant_ids = {v["id"] for v in response.data["variants"]}
        self.assertEqual(variant_ids, {p.pk for p in self.products.values()})

    def test_variant_payload_shape(self):
        response = self.client.get(self.url(self.white_product.pk))
        variant = next(
            v
            for v in response.data["variants"]
            if v["id"] == self.white_product.pk
        )
        expected = {
            "id",
            "translations",
            "slug",
            "active",
            "stock",
            "price",
            "final_price",
            "discount_percent",
            "main_image_path",
            "attribute_values",
        }
        self.assertTrue(expected.issubset(set(variant.keys())))
        # attribute_values contains only the variant-axis value (Colour),
        # never the plain spec attribute (Material).
        axis_names = {
            av["attribute_name"] for av in variant["attribute_values"]
        }
        self.assertEqual(axis_names, {"Χρώμα"})

    def test_only_variant_attributes_become_axes(self):
        response = self.client.get(self.url(self.white_product.pk))

        axes = response.data["axes"]
        self.assertEqual(len(axes), 1)
        axis = axes[0]
        self.assertEqual(axis["name"], "Χρώμα")
        self.assertEqual(axis["id"], self.colour.id)

        # Values are ordered by sort_order: Λευκό, Μαύρο, Μπλε.
        self.assertEqual(
            [v["value"] for v in axis["values"]],
            ["Λευκό", "Μαύρο", "Μπλε"],
        )

    def test_ungrouped_product_returns_itself_only(self):
        response = self.client.get(self.url(self.lonely.pk))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["variants"]), 1)
        self.assertEqual(response.data["variants"][0]["id"], self.lonely.pk)
        # No variant attributes assigned → no axes.
        self.assertEqual(response.data["axes"], [])

    def test_inactive_siblings_are_excluded(self):
        # Deactivate the blue variant; it should drop out of the payload.
        blue_product = self.products[self.blue.id]
        blue_product.active = False
        blue_product.save(update_fields=["active"])

        response = self.client.get(self.url(self.white_product.pk))
        variant_ids = {v["id"] for v in response.data["variants"]}
        self.assertNotIn(blue_product.pk, variant_ids)
        self.assertEqual(len(variant_ids), 2)

    def test_no_n_plus_one_queries(self):
        """Query count must not grow with the number of siblings."""
        with count_queries() as small:
            self.client.get(self.url(self.white_product.pk))

        # Add three more colour variants to the same group.
        for i in range(3):
            extra_value = _value(
                self.colour, f"Επιπλέον {i}", sort_order=10 + i
            )
            product = ProductFactory(
                category=self.category,
                vat=self.vat,
                price="9.00",
                stock=3,
                active=True,
                variant_group=self.group,
            )
            ProductImageFactory(product=product, is_main=True)
            ProductAttributeFactory(
                product=product, attribute_value=extra_value
            )

        with count_queries() as large:
            self.client.get(self.url(self.white_product.pk))

        self.assertEqual(
            small.count,
            large.count,
            f"Query count grew from {small.count} to {large.count} when "
            f"siblings doubled — N+1 regression.",
        )

    def test_product_detail_exposes_variant_group(self):
        response = self.client.get(
            reverse("product-detail", args=[self.white_product.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["variant_group"], self.group.pk)
        self.assertIsNone(
            self.client.get(
                reverse("product-detail", args=[self.lonely.pk])
            ).data["variant_group"]
        )

    def test_product_attribute_unique_constraint_still_holds(self):
        # Guard: a product cannot carry the same attribute value twice.
        count = ProductAttribute.objects.filter(
            product=self.white_product
        ).count()
        self.assertEqual(count, 2)  # one colour + one material
