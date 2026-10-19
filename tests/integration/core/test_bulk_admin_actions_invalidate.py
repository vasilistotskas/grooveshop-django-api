"""A bulk admin action must refresh the storefront like a single save.

``queryset.update`` emits no ``post_save``, so activating, publishing or
featuring rows in bulk left the storefront cache surface (and the price
and search refresh behind the same signals) untouched until the TTL ran
out. The actions now save each row; this pins that every one marks its
surface stale, and that a row already in the target state does not.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from blog.admin import BlogPostAdmin
from blog.factories.post import BlogPostFactory
from blog.models import BlogPost
from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
)
from product.admin import ProductAdmin
from product.factories.product import ProductFactory
from product.models.product import Product
from promotion.admin import PromotionAdmin
from promotion.factories.promotion import PromotionFactory
from promotion.models import Promotion
from tag.admin import TagAdmin
from tag.factories import TagFactory
from tag.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _connected():
    _pending_codes().clear()
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()
    _pending_codes().clear()


@pytest.fixture
def purged():
    """The surface codes handed to ``CacheService.purge``, flattened."""
    with mock.patch("core.cache.service.CacheService.purge") as purge:
        purge.return_value = mock.Mock(
            failed_surfaces=[], django_headline=0, nuxt_headline=0
        )

        def codes() -> set[str]:
            return {
                code for call in purge.call_args_list for code in call.args[0]
            }

        # Building the fixture rows purged too; only the action counts.
        codes.reset = purge.reset_mock
        yield codes


def _run(admin_class, model, action, queryset):
    admin = admin_class(model, AdminSite())
    request = RequestFactory().post("/admin/")
    request.user = None
    with mock.patch.object(admin_class, "message_user"):
        getattr(admin, action)(request, queryset)


class TestBulkActionsMarkTheSurfaceStale:
    def test_deactivating_products(self, purged):
        product = ProductFactory(active=True, num_images=0, num_reviews=0)
        _pending_codes().clear()
        purged.reset()

        _run(
            ProductAdmin,
            Product,
            "make_inactive",
            Product.objects.filter(pk=product.pk),
        )

        assert "products" in purged()
        assert not Product.objects.get(pk=product.pk).active

    def test_activating_products(self, purged):
        product = ProductFactory(active=False, num_images=0, num_reviews=0)
        _pending_codes().clear()
        purged.reset()

        _run(
            ProductAdmin,
            Product,
            "make_active",
            Product.objects.filter(pk=product.pk),
        )

        assert "products" in purged()
        assert Product.objects.get(pk=product.pk).active

    @pytest.mark.parametrize(
        ("action", "field", "value"),
        [
            ("publish_posts", "is_published", True),
            ("mark_as_featured", "featured", True),
        ],
    )
    def test_blog_actions(self, purged, action, field, value):
        post = BlogPostFactory(is_published=False, featured=False)
        _pending_codes().clear()
        purged.reset()

        _run(
            BlogPostAdmin, BlogPost, action, BlogPost.objects.filter(pk=post.pk)
        )

        assert "blog" in purged()
        assert getattr(BlogPost.objects.get(pk=post.pk), field) is value

    def test_unpublishing_and_unfeaturing_posts(self, purged):
        post = BlogPostFactory(is_published=True, featured=True)
        _pending_codes().clear()
        purged.reset()
        queryset = BlogPost.objects.filter(pk=post.pk)

        _run(BlogPostAdmin, BlogPost, "unpublish_posts", queryset)
        _run(BlogPostAdmin, BlogPost, "unmark_as_featured", queryset)

        assert "blog" in purged()
        post.refresh_from_db()
        assert not post.is_published
        assert not post.featured

    @pytest.mark.parametrize(
        ("action", "start", "end"),
        [
            ("activate_promotions", False, True),
            ("deactivate_promotions", True, False),
        ],
    )
    def test_promotion_actions(self, purged, action, start, end):
        promotion = PromotionFactory(is_active=start)
        _pending_codes().clear()
        purged.reset()

        _run(
            PromotionAdmin,
            Promotion,
            action,
            Promotion.objects.filter(pk=promotion.pk),
        )

        assert "promotions" in purged()
        assert Promotion.objects.get(pk=promotion.pk).is_active is end

    @pytest.mark.parametrize(
        ("action", "start", "end"),
        [("activate_tags", False, True), ("deactivate_tags", True, False)],
    )
    def test_tag_actions(self, purged, action, start, end):
        tag = TagFactory(active=start)
        _pending_codes().clear()
        purged.reset()

        _run(TagAdmin, Tag, action, Tag.objects.filter(pk=tag.pk))

        assert "tags" in purged()
        assert Tag.objects.get(pk=tag.pk).active is end


def test_a_row_already_in_the_target_state_purges_nothing(purged):
    tag = TagFactory(active=True)
    _pending_codes().clear()
    purged.reset()

    _run(TagAdmin, Tag, "activate_tags", Tag.objects.filter(pk=tag.pk))

    assert purged() == set()
