"""Star distribution on the product page and the verified-purchase flag."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.enum.review import RateEnum, ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from tests.utils import TestURLFixerMixin
from user.factories.account import UserAccountFactory


def _order_with(user, product, order_status):
    order = OrderFactory.create(
        user=user, status=order_status, num_order_items=0
    )
    OrderItemFactory.create(order=order, product=product, quantity=1)
    return order


class ReviewSummaryTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = ProductFactory.create(stock=10, active=True)
        cls.other_product = ProductFactory.create(stock=10, active=True)

        cls.buyer = UserAccountFactory()
        cls.paid_only = UserAccountFactory()
        cls.canceled = UserAccountFactory()
        cls.stranger = UserAccountFactory()
        cls.other_buyer = UserAccountFactory()
        cls.rejected_author = UserAccountFactory()

        _order_with(cls.buyer, cls.product, OrderStatus.COMPLETED)
        _order_with(cls.paid_only, cls.product, OrderStatus.PROCESSING)
        _order_with(cls.canceled, cls.product, OrderStatus.CANCELED)
        # Completed, but for ANOTHER product: proves nothing here.
        _order_with(cls.other_buyer, cls.other_product, OrderStatus.COMPLETED)

        cls.reviews = {
            user: ProductReviewFactory.create(
                product=cls.product,
                user=user,
                rate=rate,
                status=ReviewStatus.TRUE,
            )
            for user, rate in (
                (cls.buyer, 5),
                (cls.paid_only, 5),
                (cls.canceled, 4),
                (cls.stranger, 1),
                (cls.other_buyer, 5),
            )
        }
        ProductReviewFactory.create(
            product=cls.product,
            user=cls.rejected_author,
            rate=2,
            status=ReviewStatus.FALSE,
        )

    def test_distribution_counts_approved_reviews_only(self):
        response = self.client.get(
            reverse("product-detail", args=[self.product.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        distribution = {
            row["rate"]: row["count"]
            for row in response.data["rating_distribution"]
        }
        self.assertEqual(set(distribution), set(RateEnum.values))
        self.assertEqual(distribution[5], 3)
        self.assertEqual(distribution[4], 1)
        self.assertEqual(distribution[1], 1)
        self.assertEqual(distribution[2], 0)

    def test_average_and_count_agree_with_the_distribution(self):
        """A rejected review (rate 2) and a pending one never move the
        public average or count; the three figures describe the same
        approved set."""
        ProductReviewFactory.create(
            product=self.product,
            user=UserAccountFactory(),
            rate=1,
            status=ReviewStatus.NEW,
        )

        for name, args in (
            ("product-detail", [self.product.pk]),
            ("product-list", []),
        ):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.data
            row = (
                data
                if name == "product-detail"
                else next(
                    r for r in data["results"] if r["id"] == self.product.pk
                )
            )
            self.assertEqual(row["review_count"], 5, name)
            self.assertAlmostEqual(row["review_average"], 4.0, msg=name)

    def test_verified_purchase_requires_completed_order_of_this_product(self):
        response = self.client.get(
            reverse("product-reviews", args=[self.product.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        flags = {
            row["user"]["id"]: row["is_verified_purchase"]
            for row in response.data["results"]
        }
        self.assertEqual(
            flags,
            {
                self.buyer.id: True,
                self.paid_only.id: False,
                self.canceled.id: False,
                self.stranger.id: False,
                self.other_buyer.id: False,
            },
        )

    def test_reviews_list_query_count_is_flat_in_review_count(self):
        url = reverse("product-reviews", args=[self.product.pk])

        def add_reviews(count):
            for _ in range(count):
                user = UserAccountFactory()
                _order_with(user, self.product, OrderStatus.COMPLETED)
                ProductReviewFactory.create(
                    product=self.product, user=user, status=ReviewStatus.TRUE
                )
            # Factories clear the ContentType cache; re-prime it so the
            # measurement sees only the endpoint's own queries.
            self.client.get(url)

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx)

        add_reviews(1)
        before = count_queries()
        add_reviews(2)

        with self.assertNumQueries(before):
            self.client.get(url)

    def test_user_product_review_carries_the_flag(self):
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(
            reverse(
                "product-review-user-product-review", args=[self.product.pk]
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_verified_purchase"])
