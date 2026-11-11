"""A changed record reaches the pages that show it, and only those.

``BuilderReference`` says which documents of the current release name a
record or ask for a list it can enter; ``builder.data_purge`` turns a save or
a delete into those document keys and queues them for the batched purge.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from builder import data_purge
from builder.models import BuilderReference, ReferenceKind
from product.factories.category import ProductCategoryFactory
from product.models.product import Product
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def queued():
    """The documents each change would queue, one set per change."""
    data_purge.connect(force=True)
    with mock.patch("builder.data_purge.record") as record:
        yield record
    data_purge.disconnect()


def index(kind, target, document):
    BuilderReference.objects.create(
        kind=kind, target=str(target), document=document, node_path="/x"
    )


def last(queued) -> set[str]:
    assert queued.call_count >= 1, "nothing was queued"
    return set(queued.call_args.args[0])


def product(category=None, **fields):
    return Product.objects.create(
        sku=fields.pop("sku", "P-1"),
        price=Decimal("10.00"),
        active=True,
        stock=fields.pop("stock", 5),
        vat=VatFactory(value=Decimal("20.0")),
        category=category,
        **fields,
    )


@pytest.fixture
def pages():
    """Rows of the current release: lists bounded by categories, every
    product, offers."""
    index(ReferenceKind.QUERY_PRODUCT, "*", "page:everything")
    index(ReferenceKind.QUERY_PRODUCT, "category:11", "page:in-11")
    index(ReferenceKind.QUERY_PRODUCT, "category:12", "page:in-12")
    index(ReferenceKind.QUERY_PROMOTION, "*", "page:offers")
    index(ReferenceKind.QUERY_BLOG_POST, "*", "page:all-posts")
    index(ReferenceKind.QUERY_BLOG_POST, "category:7", "page:in-blog-7")


class TestAProduct:
    def test_a_product_reaches_the_pages_that_name_it_and_every_open_list(
        self, queued, pages
    ):
        item = product()
        index(ReferenceKind.PRODUCT, item.pk, "template:home")
        queued.reset_mock()

        item.price = Decimal("12.00")
        item.save()

        assert last(queued) == {"template:home", "page:everything"}

    def test_a_list_bounded_by_a_category_is_reached_through_its_ancestors(
        self, queued
    ):
        top = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=top)
        index(ReferenceKind.QUERY_PRODUCT, f"category:{top.pk}", "page:top")
        index(ReferenceKind.QUERY_PRODUCT, f"category:{child.pk}", "page:child")
        index(ReferenceKind.QUERY_PRODUCT, "category:999999", "page:elsewhere")

        product(category=child)

        assert last(queued) == {"page:top", "page:child"}

    def test_a_product_that_moves_reaches_the_lists_it_left_and_entered(
        self, queued
    ):
        old, new = ProductCategoryFactory(), ProductCategoryFactory()
        index(ReferenceKind.QUERY_PRODUCT, f"category:{old.pk}", "page:old")
        index(ReferenceKind.QUERY_PRODUCT, f"category:{new.pk}", "page:new")
        item = product(category=old)
        queued.reset_mock()

        item.category = new
        item.save()

        assert last(queued) == {"page:old", "page:new"}

    def test_a_stock_only_save_that_stays_in_stock_queues_nothing(
        self, queued, pages
    ):
        item = product()
        queued.reset_mock()

        item.stock = 4
        item.save(update_fields=["stock", "updated_at"])

        queued.assert_not_called()

    def test_a_sale_that_sells_the_product_out_reaches_its_pages(
        self, queued, pages
    ):
        item = product()
        index(ReferenceKind.PRODUCT, item.pk, "template:home")
        queued.reset_mock()

        item.stock = 0
        item.save(update_fields=["stock", "updated_at"])

        assert last(queued) == {"template:home", "page:everything"}
        assert queued.call_count == 1

    def test_a_restock_reaches_the_same_pages(self, queued, pages):
        item = product(stock=0)
        index(ReferenceKind.PRODUCT, item.pk, "template:home")
        queued.reset_mock()

        item.stock = 3
        item.save(update_fields=["stock"])

        assert last(queued) == {"template:home", "page:everything"}
        assert queued.call_count == 1

    def test_a_stock_only_save_that_stays_sold_out_queues_nothing(
        self, queued, pages
    ):
        item = product(stock=0)
        queued.reset_mock()

        item.stock = 0
        item.save(update_fields=["stock", "updated_at"])

        queued.assert_not_called()

    def test_an_updated_at_only_save_queues_nothing(self, queued, pages):
        item = product(stock=0)
        queued.reset_mock()

        item.save(update_fields=["updated_at"])

        queued.assert_not_called()

    def test_a_full_save_that_sells_out_queues_its_pages_once(
        self, queued, pages
    ):
        item = product()
        queued.reset_mock()

        item.stock = 0
        item.save()

        assert last(queued) == {"page:everything"}
        assert queued.call_count == 1

    def test_a_stock_only_save_costs_no_query_for_the_purge(self, queued):
        """Ten queries is what the save costs without ``builder.data_purge``
        (history row, stock log, store settings): the one query that reads
        the stored category also reads the stock."""
        item = product()
        item.stock = 4

        with CaptureQueriesContext(connection) as queries:
            item.save(update_fields=["stock", "updated_at"])

        assert len(queries) == 10

    def test_deleting_a_product_reaches_the_same_pages(self, queued, pages):
        item = product()
        index(ReferenceKind.PRODUCT, item.pk, "template:home")
        queued.reset_mock()

        item.delete()

        assert {"template:home", "page:everything"} <= last(queued)


class TestACategory:
    def test_a_category_reaches_the_pages_that_name_it_and_list_categories(
        self, queued
    ):
        category = ProductCategoryFactory()
        index(ReferenceKind.CATEGORY, category.pk, "page:names-it")
        index(ReferenceKind.QUERY_CATEGORY, "*", "group:header")
        index(
            ReferenceKind.QUERY_PRODUCT,
            f"category:{category.pk}",
            "page:products-of-it",
        )
        queued.reset_mock()

        category.save()

        assert last(queued) == {
            "page:names-it",
            "group:header",
            "page:products-of-it",
        }

    def test_a_child_list_is_reached_when_a_child_is_renamed(self, queued):
        parent = ProductCategoryFactory()
        child = ProductCategoryFactory(parent=parent)
        index(ReferenceKind.QUERY_CATEGORY, f"parent:{parent.pk}", "group:menu")
        index(ReferenceKind.QUERY_CATEGORY, "parent:999999", "group:other")
        queued.reset_mock()

        child.save()

        assert "group:menu" in last(queued)
        assert "group:other" not in last(queued)


class TestAPromotion:
    def test_a_promotion_reaches_the_offers_and_every_page_showing_a_price(
        self, queued, pages
    ):
        from promotion.factories.promotion import PromotionFactory

        index(ReferenceKind.PRODUCT, 1, "template:home")
        queued.reset_mock()

        PromotionFactory()

        assert last(queued) >= {
            "page:offers",
            "template:home",
            "page:everything",
            "page:in-11",
        }
        assert "page:all-posts" not in last(queued)


class TestABlogPost:
    def test_a_post_reaches_its_page_and_the_lists_of_its_category(
        self, queued
    ):
        from blog.factories.category import BlogCategoryFactory
        from blog.factories.post import BlogPostFactory

        category = BlogCategoryFactory()
        index(ReferenceKind.QUERY_BLOG_POST, "*", "page:all-posts")
        index(
            ReferenceKind.QUERY_BLOG_POST,
            f"category:{category.pk}",
            "page:in-category",
        )
        index(
            ReferenceKind.QUERY_BLOG_POST, "category:999999", "page:elsewhere"
        )
        post = BlogPostFactory(category=category)
        index(ReferenceKind.BLOG_POST, post.pk, "template:blog")
        queued.reset_mock()

        post.save()

        assert last(queued) == {
            "page:all-posts",
            "page:in-category",
            "template:blog",
        }

    def test_a_post_going_live_reaches_the_lists_of_its_category(self, queued):
        """A post dated in the future reaches its date with no save; the
        publication sweep's ``went_live`` stands in for it."""
        from blog.factories.category import BlogCategoryFactory
        from blog.factories.post import BlogPostFactory
        from blog.models.post import BlogPost
        from core.publication import went_live

        category = BlogCategoryFactory()
        index(
            ReferenceKind.QUERY_BLOG_POST,
            f"category:{category.pk}",
            "page:in-category",
        )
        post = BlogPostFactory(category=category)
        queued.reset_mock()

        went_live.send(sender=BlogPost, instance=post)

        assert last(queued) == {"page:in-category"}

    def test_another_model_going_live_queues_nothing(self, queued):
        from blog.factories.post import BlogPostFactory
        from core.publication import went_live
        from page_config.models import ContentPage

        post = BlogPostFactory()
        queued.reset_mock()

        went_live.send(sender=ContentPage, instance=post)

        assert queued.call_count == 0

    def test_a_blog_category_reaches_the_lists_of_its_posts(self, queued):
        from blog.factories.category import BlogCategoryFactory

        category = BlogCategoryFactory()
        index(ReferenceKind.BLOG_CATEGORY, category.pk, "page:names-it")
        index(ReferenceKind.QUERY_BLOG_CATEGORY, "*", "page:lists-them")
        index(
            ReferenceKind.QUERY_BLOG_POST,
            f"category:{category.pk}",
            "page:its-posts",
        )
        queued.reset_mock()

        category.save()

        assert last(queued) == {
            "page:names-it",
            "page:lists-them",
            "page:its-posts",
        }
