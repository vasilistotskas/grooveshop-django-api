"""A published product or blog post that is created, changed or
unpublished is queued for the next IndexNow notification; nothing else is.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import models

from blog.factories.post import BlogPostFactory
from indexnow.models import PendingUrl
from product.factories.product import ProductFactory
from tests.unit.indexnow.conftest import setting_reader

pytestmark = pytest.mark.django_db


def queued() -> set[str]:
    return set(PendingUrl.objects.values_list("path", flat=True))


def drain() -> None:
    PendingUrl.objects.all().delete()


class TestProduct:
    def test_a_published_product_is_queued_when_created(self, indexnow_on):
        product = ProductFactory(active=True)

        assert queued() == {f"/products/{product.id}/{product.slug}"}

    def test_a_published_product_is_queued_when_changed(self, indexnow_on):
        product = ProductFactory(active=True)
        drain()

        product.stock += 1
        product.save()

        assert queued() == {product.storefront_path}

    def test_a_product_is_queued_once_however_often_it_changes(
        self, indexnow_on
    ):
        product = ProductFactory(active=True)
        product.save()
        product.save()

        assert PendingUrl.objects.count() == 1

    def test_unpublishing_a_product_queues_it(self, indexnow_on):
        product = ProductFactory(active=True)
        drain()

        product.active = False
        product.save()

        assert queued() == {product.storefront_path}

    def test_deleting_a_product_queues_it(self, indexnow_on):
        product = ProductFactory(active=True)
        drain()

        product.delete()

        assert queued() == {product.storefront_path}

    def test_removing_a_product_row_queues_it(self, indexnow_on):
        product = ProductFactory(active=True)
        path = product.storefront_path
        drain()

        models.Model.delete(product)

        assert queued() == {path}

    def test_a_product_nobody_can_see_queues_nothing(self, indexnow_on):
        product = ProductFactory(active=False)
        product.name = "still a draft"
        product.save()

        assert queued() == set()

    def test_a_counter_only_save_queues_nothing(self, indexnow_on):
        product = ProductFactory(active=True)
        drain()

        product.view_count += 1
        product.save(update_fields=["view_count"])

        assert queued() == set()

    def test_a_translation_save_queues_its_product(self, indexnow_on):
        product = ProductFactory(active=True)
        drain()

        translation = product.translations.first()
        translation.name = "A new name"
        translation.save()

        assert queued() == {product.storefront_path}

    def test_a_draft_translation_queues_nothing(self, indexnow_on):
        product = ProductFactory(active=False)
        translation = product.translations.first()
        translation.name = "A new name"
        translation.save()

        assert queued() == set()


class TestBlogPost:
    def test_a_published_post_is_queued_when_created(self, indexnow_on):
        post = BlogPostFactory(is_published=True)

        assert queued() == {f"/blog/post/{post.id}/{post.slug}"}

    def test_unpublishing_a_post_queues_it(self, indexnow_on):
        post = BlogPostFactory(is_published=True)
        drain()

        post.is_published = False
        post.save()

        assert queued() == {post.storefront_path}

    def test_deleting_a_post_queues_it(self, indexnow_on):
        post = BlogPostFactory(is_published=True)
        path = post.storefront_path
        drain()

        post.delete()

        assert queued() == {path}

    def test_a_draft_queues_nothing(self, indexnow_on):
        post = BlogPostFactory(is_published=False)
        post.save()

        assert queued() == set()

    def test_a_translation_save_queues_its_post(self, indexnow_on):
        post = BlogPostFactory(is_published=True)
        drain()

        translation = post.translations.first()
        translation.title = "A new title"
        translation.save()

        assert queued() == {post.storefront_path}


class TestSwitchedOff:
    def test_a_store_that_did_not_switch_it_on_queues_nothing(self, store):
        with patch(
            "extra_settings.models.Setting.get",
            side_effect=setting_reader(),
        ):
            ProductFactory(active=True)
            BlogPostFactory(is_published=True)

        assert queued() == set()

    def test_the_control_plane_queues_nothing(
        self, store, bind_tenant_and_schema
    ):
        bind_tenant_and_schema(None)
        with patch(
            "extra_settings.models.Setting.get",
            side_effect=setting_reader(INDEXNOW_ENABLED=True),
        ):
            ProductFactory(active=True)

        assert queued() == set()
