import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.category import ProductCategoryFactory
from product.factories.favourite import ProductFavouriteFactory
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from product.serializers.product import (
    ProductDetailSerializer,
)
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductViewSetTestCase(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.category = ProductCategoryFactory()
        self.vat = VatFactory()
        self.product = ProductFactory(
            category=self.category,
            vat=self.vat,
        )

        self.images = []
        main_product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )
        self.images.append(main_product_image)

        non_main_product_image = ProductImageFactory(
            product=self.product,
            is_main=False,
        )
        self.images.append(non_main_product_image)

        self.favourite = ProductFavouriteFactory(
            product=self.product,
            user=self.user,
        )

        user_2 = UserAccountFactory(num_addresses=0)

        self.reviews = []
        product_review_status_true = ProductReviewFactory(
            product=self.product,
            user=self.user,
            status="True",
        )
        self.reviews.append(product_review_status_true)

        product_review_status_false = ProductReviewFactory(
            product=self.product,
            user=user_2,
            status="False",
        )
        self.reviews.append(product_review_status_false)

    def get_product_detail_url(self, pk):
        return reverse("product-detail", args=[pk])

    def get_product_list_url(self):
        return reverse("product-list")

    def test_list_uses_correct_serializer(self):
        url = self.get_product_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            product_data = response.data["results"][0]
            expected_fields = {
                "id",
                "slug",
                "category",
                "price",
                "vat",
                "view_count",
                "stock",
                "active",
                "translations",
                "final_price",
                "discount_value",
                "review_average",
                "review_count",
                "likes_count",
            }
            self.assertTrue(expected_fields.issubset(set(product_data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_detail_url(self.product.pk)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "translations",
            "final_price",
            "discount_value",
            "review_average",
            "review_count",
            "likes_count",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        payload = {
            "slug": "test-product-new",
            "category": self.category.pk,
            "translations": json.dumps(
                {
                    default_language: {
                        "name": "Test Product Name",
                        "description": "Test Product Description",
                    },
                }
            ),
            "price": "100.00",
            "active": True,
            "stock": 10,
            "discount_percent": 0,
            "vat": self.vat.pk,
            "weight": {
                "value": "5.00",
                "unit": "kg",
            },
        }

        url = self.get_product_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "translations",
            "final_price",
            "discount_value",
            "review_average",
            "review_count",
            "likes_count",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        product = Product.objects.get(id=response.data["id"])
        self.assertEqual(product.slug, "test-product-new")
        self.assertEqual(product.category.id, self.category.id)

    def test_update_request_response_serializers(self):
        payload = {
            "slug": "updated-product-slug",
            "category": self.category.pk,
            "translations": json.dumps(
                {
                    default_language: {
                        "name": "Updated Product Name",
                        "description": "Updated Product Description",
                    },
                }
            ),
            "price": "150.00",
            "active": True,
            "stock": 20,
            "discount_percent": 10,
            "vat": self.vat.pk,
            "weight": {
                "value": "3.00",
                "unit": "kg",
            },
        }

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "translations",
            "final_price",
            "discount_value",
            "review_average",
            "review_count",
            "likes_count",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        product = Product.objects.get(id=response.data["id"])
        self.assertEqual(product.slug, "updated-product-slug")
        self.assertEqual(product.stock, 20)

    def test_partial_update_request_response_serializers(self):
        payload = {
            "active": False,
            "stock": 5,
        }

        url = self.get_product_detail_url(self.product.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "translations",
            "final_price",
            "discount_value",
            "review_average",
            "review_count",
            "likes_count",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        product = Product.objects.get(id=response.data["id"])
        self.assertEqual(product.active, False)
        self.assertEqual(product.stock, 5)

    def test_filtering_functionality(self):
        test_category = ProductCategoryFactory()

        expensive_product = ProductFactory(
            category=test_category,
            vat=self.vat,
            slug="expensive-product-test",
            active=True,
        )
        expensive_product.price = "500.00"
        expensive_product.save()

        inactive_product = ProductFactory(
            category=test_category,
            vat=self.vat,
            active=False,
            slug="inactive-product-test",
        )

        url = self.get_product_list_url()

        response = self.client.get(url, {"category": test_category.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_ids = [prod["id"] for prod in response.data["results"]]
        self.assertIn(expensive_product.id, product_ids)
        self.assertIn(inactive_product.id, product_ids)

        response = self.client.get(url, {"active": True})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        active_products = response.data["results"]

        for product in active_products:
            self.assertTrue(product["active"])

        active_ids = [prod["id"] for prod in active_products]
        self.assertIn(expensive_product.id, active_ids)
        self.assertNotIn(inactive_product.id, active_ids)

    def test_search_functionality(self):
        searchable_product = ProductFactory(
            category=self.category, vat=self.vat, slug="searchable-product"
        )
        searchable_product.set_current_language(default_language)
        searchable_product.name = "Unique Searchable Product Name"
        searchable_product.description = "Special description content"
        searchable_product.save()

        url = self.get_product_list_url()

        response = self.client.get(url, {"search": "Unique"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_ids = [prod["id"] for prod in response.data["results"]]
        self.assertIn(searchable_product.id, product_ids)

        response = self.client.get(url, {"search": "Special"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_ids = [prod["id"] for prod in response.data["results"]]
        self.assertIn(searchable_product.id, product_ids)

        response = self.client.get(url, {"search": "searchable-product"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product_ids = [prod["id"] for prod in response.data["results"]]
        self.assertIn(searchable_product.id, product_ids)

    def test_ordering_functionality(self):
        url = self.get_product_list_url()

        response = self.client.get(url, {"ordering": "price"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "-created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if len(response.data["results"]) > 1:
            first_created = response.data["results"][0]["created_at"]
            second_created = response.data["results"][1]["created_at"]
            self.assertGreaterEqual(first_created, second_created)

    def test_validation_errors_consistent(self):
        payload = {
            "slug": "test-product-invalid",
            "category": self.category.pk,
            "translations": json.dumps(
                {
                    default_language: {
                        "name": "Test Product",
                        "description": "Test Description",
                    },
                }
            ),
            "price": "-100.00",
            "active": True,
            "stock": 10,
            "vat": self.vat.pk,
        }

        url = self.get_product_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("price", response.data)

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_product_detail_url(self.product.pk)
        viewset_response = self.client.get(url)

        manual_serializer = ProductDetailSerializer(
            self.product, context={"request": viewset_response.wsgi_request}
        )

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        key_fields = ["id", "slug", "category", "price", "active"]
        for field in key_fields:
            self.assertEqual(
                viewset_response.data[field], manual_serializer.data[field]
            )

    def test_update_view_count_action(self):
        initial_count = self.product.view_count
        url = reverse("product-update-view-count", args=[self.product.pk])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.product.refresh_from_db()
        self.assertEqual(self.product.view_count, initial_count + 1)

        expected_fields = {
            "id",
            "slug",
            "category",
            "price",
            "vat",
            "view_count",
            "stock",
            "active",
            "translations",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_reviews_action(self):
        url = reverse("product-reviews", args=[self.product.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

        product_reviews = [
            r for r in self.reviews if r.product_id == self.product.pk
        ]
        self.assertEqual(len(response.data), len(product_reviews))

        if response.data:
            review_data = response.data[0]
            expected_fields = {"id", "product", "user", "rate", "status"}
            self.assertTrue(expected_fields.issubset(set(review_data.keys())))

    def test_images_action(self):
        url = reverse("product-images", args=[self.product.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

        product_images = [
            img for img in self.images if img.product_id == self.product.pk
        ]
        self.assertEqual(len(response.data), len(product_images))

        if response.data:
            image_data = response.data[0]
            expected_fields = {"id", "product", "image", "is_main"}
            self.assertTrue(expected_fields.issubset(set(image_data.keys())))

    def test_tags_action(self):
        url = reverse("product-tags", args=[self.product.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

        self.assertEqual(len(response.data), 0)

    def test_create_with_complex_payload(self):
        payload = {
            "slug": "complex-product-test",
            "category": self.category.pk,
            "translations": json.dumps(
                {
                    "en": {
                        "name": "Complex Product EN",
                        "description": "Complex product description EN",
                    },
                    "de": {
                        "name": "Complex Product DE",
                        "description": "Complex product description DE",
                    },
                }
            ),
            "price": "250.00",
            "active": True,
            "stock": 15,
            "discount_percent": 15,
            "vat": self.vat.pk,
            "weight": {
                "value": "2.50",
                "unit": "kg",
            },
            "seo_title": "Complex Product SEO Title",
            "seo_description": "Complex Product SEO Description",
            "seo_keywords": "complex, product, test",
        }

        url = self.get_product_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        product = Product.objects.get(id=response.data["id"])

        if "en" in [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]:
            product.set_current_language("en")
            self.assertEqual(product.name, "Complex Product EN")

        if "de" in [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]:
            product.set_current_language("de")
            self.assertEqual(product.name, "Complex Product DE")

    def test_queryset_optimization(self):
        url = self.get_product_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ordering_by_discount_value(self):
        for _ in range(5):
            ProductFactory(
                category=self.category,
                vat=self.vat,
            )

        url = self.get_product_list_url()
        response = self.client.get(url, {"ordering": "discount_value_amount"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        products = response.data["results"]

        for i in range(len(products) - 1):
            current_discount = float(products[i]["discount_value"])
            next_discount = float(products[i + 1]["discount_value"])
            self.assertLessEqual(current_discount, next_discount)

    def test_ordering_by_final_price(self):
        for _ in range(5):
            ProductFactory(
                category=self.category,
                vat=self.vat,
            )

        url = self.get_product_list_url()
        response = self.client.get(url, {"ordering": "final_price_amount"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        products = response.data["results"]

        for i in range(len(products) - 1):
            current_price = float(products[i]["final_price"])
            next_price = float(products[i + 1]["final_price"])
            self.assertLessEqual(current_price, next_price)

    def test_ordering_by_review_average(self):
        products = []
        for _i in range(5):
            product = ProductFactory(
                category=self.category,
                num_images=0,
                num_reviews=0,
            )
            products.append(product)

        for index, product in enumerate(products):
            rating = 5 - index
            ProductReviewFactory(product=product, rate=rating)

        url = self.get_product_list_url()
        response = self.client.get(url, {"ordering": "-review_average_field"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]

        for i in range(len(results) - 1):
            current_avg = results[i].get("review_average", 0)
            next_avg = results[i + 1].get("review_average", 0)
            self.assertGreaterEqual(current_avg, next_avg)

    def test_ordering_by_likes_count(self):
        products = []
        for _i in range(5):
            product = ProductFactory(
                category=self.category,
                num_images=0,
                num_reviews=0,
            )
            products.append(product)

        users = []
        for _i in range(5):
            user = UserAccountFactory(num_addresses=0)
            users.append(user)

        for i, product in enumerate(products):
            for j in range(i + 1):
                ProductFavouriteFactory(
                    product=product,
                    user=users[j],
                )

        url = self.get_product_list_url()
        response = self.client.get(url, {"ordering": "likes_count_field"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_filter_by_price_range(self):
        test_products = []
        for i in range(3):
            price = (i + 2) * 100
            product = ProductFactory(
                category=self.category,
                vat=self.vat,
                price=f"{price}.00",
            )
            test_products.append(product)

        url = self.get_product_list_url()
        response = self.client.get(
            url, {"min_final_price": "200", "max_final_price": "400"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        products = response.data["results"]

        for product in products:
            final_price = float(product["final_price"])
            self.assertGreaterEqual(final_price, 200)
            self.assertLessEqual(final_price, 400)
