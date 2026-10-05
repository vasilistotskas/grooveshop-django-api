from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.brand import BrandFactory
from product.factories.product import ProductFactory


def _product(brand, **kwargs):
    return ProductFactory(brand=brand, num_images=0, num_reviews=0, **kwargs)


class BrandViewSetTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zeta = BrandFactory(name="Zeta")
        cls.alpha = BrandFactory(name="Alpha")
        cls.mid = BrandFactory(name="Mid")
        cls.dormant = BrandFactory(name="Dormant")
        cls.switched_off = BrandFactory(name="SwitchedOff")
        cls.deleted = BrandFactory(name="Deleted")

        _product(cls.zeta, active=True)
        _product(cls.alpha, active=True)
        _product(cls.alpha, active=True)
        _product(cls.mid, active=True)
        _product(cls.switched_off, active=False)
        _product(cls.deleted, active=True, is_deleted=True)

    def _names(self, rows):
        return [row["name"] for row in rows]

    def test_anonymous_list_is_allowed_with_the_minimal_shape(self):
        response = self.client.get(reverse("product-brand-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data["results"][0]
        self.assertEqual(set(row), {"id", "name"})

    def test_list_is_ordered_by_name(self):
        response = self.client.get(reverse("product-brand-list"))

        self.assertEqual(
            self._names(response.data["results"]), ["Alpha", "Mid", "Zeta"]
        )

    def test_list_omits_brands_without_an_active_product(self):
        response = self.client.get(reverse("product-brand-list"))

        names = self._names(response.data["results"])
        for name in ("Dormant", "SwitchedOff", "Deleted"):
            self.assertNotIn(name, names)

    def test_brand_with_several_products_is_listed_once(self):
        response = self.client.get(reverse("product-brand-list"))

        self.assertEqual(
            self._names(response.data["results"]).count("Alpha"), 1
        )

    def test_all_is_unpaginated_and_matches_the_list(self):
        response = self.client.get(reverse("product-brand-all"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(self._names(response.data), ["Alpha", "Mid", "Zeta"])
        self.assertEqual(
            response.data[0], {"id": self.alpha.id, "name": "Alpha"}
        )

    def test_all_is_not_capped_by_the_page_size(self):
        for i in range(30):
            _product(BrandFactory(name=f"Bulk {i:02d}"), active=True)

        response = self.client.get(reverse("product-brand-all"))

        self.assertEqual(len(response.data), 33)

    def test_retrieve_returns_the_brand(self):
        response = self.client.get(
            reverse("product-brand-detail", args=[self.mid.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"id": self.mid.id, "name": "Mid"})

    def test_retrieve_of_a_brand_without_active_products_is_404(self):
        response = self.client.get(
            reverse("product-brand-detail", args=[self.dormant.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_writes_are_not_routed(self):
        response = self.client.post(
            reverse("product-brand-list"), {"name": "New"}
        )

        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def test_query_count_does_not_grow_with_rows(self):
        # One SELECT for the rows; the active-product gate is a
        # correlated EXISTS, not a query per brand.
        for name in ("product-brand-list", "product-brand-all"):
            with self.assertNumQueries(2 if name.endswith("list") else 1):
                self.client.get(reverse(name))

        for i in range(5):
            _product(BrandFactory(name=f"Extra {i}"), active=True)

        for name in ("product-brand-list", "product-brand-all"):
            with self.assertNumQueries(2 if name.endswith("list") else 1):
                self.client.get(reverse(name))
