"""A change the storefront can see must purge its cache surface.

The bulk admin actions (#259) were fixed to save per row, but these
paths still left the cached storefront stale until the TTL:

* moderating a review or a blog comment (``queryset.update``, and the
  models were not on any surface);
* a coupon code, which the offers listing prints, and which the admin
  generates with ``bulk_create``;
* a promotion window opening or closing, where nothing is saved;
* a VAT edit, which reprices products with ``bulk_update``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone

from blog.admin import BlogCommentAdmin
from blog.factories.comment import BlogCommentFactory
from blog.models import BlogComment
from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
)
from product.admin import ProductReviewAdmin
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.review import ProductReview
from promotion import offer_sync
from promotion.admin import _create_codes
from promotion.factories.promotion import (
    PromotionCodeFactory,
    PromotionFactory,
)
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _connected(settings):
    settings.DISABLE_CACHE = False
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

        # Building the fixture rows purged too; only the change counts.
        codes.reset = purge.reset_mock
        yield codes


def _admin_call(admin_class, model, name, *args):
    admin = admin_class(model, AdminSite())
    request = RequestFactory().post("/admin/")
    request.user = None
    with (
        mock.patch.object(admin_class, "message_user"),
        mock.patch.object(admin_class, "_back_to", create=True),
    ):
        return getattr(admin, name)(request, *args)


class TestModeration:
    @pytest.mark.parametrize(
        ("action", "status"),
        [
            ("approve_reviews", ReviewStatus.TRUE),
            ("reject_reviews", ReviewStatus.FALSE),
        ],
    )
    def test_bulk_moderating_reviews(self, purged, action, status):
        review = ProductReviewFactory(
            status=ReviewStatus.NEW
            if status == ReviewStatus.TRUE
            else ReviewStatus.TRUE
        )
        purged.reset()

        _admin_call(
            ProductReviewAdmin,
            ProductReview,
            action,
            ProductReview.objects.filter(pk=review.pk),
        )

        assert "products" in purged()
        review.refresh_from_db()
        assert review.status == status
        assert review.is_published is (status == ReviewStatus.TRUE)

    @pytest.mark.parametrize(
        ("action", "status"),
        [
            ("approve_review", ReviewStatus.TRUE),
            ("reject_review", ReviewStatus.FALSE),
        ],
    )
    def test_moderating_one_review(self, purged, action, status):
        review = ProductReviewFactory(
            status=ReviewStatus.NEW
            if status == ReviewStatus.TRUE
            else ReviewStatus.TRUE
        )
        purged.reset()

        _admin_call(ProductReviewAdmin, ProductReview, action, str(review.pk))

        assert "products" in purged()

    @pytest.mark.parametrize(
        ("action", "start"),
        [("approve_comments", False), ("unapprove_comments", True)],
    )
    def test_moderating_comments(self, purged, action, start):
        comment = BlogCommentFactory(approved=start)
        purged.reset()

        _admin_call(
            BlogCommentAdmin,
            BlogComment,
            action,
            BlogComment.objects.filter(pk=comment.pk),
        )

        assert "blog" in purged()
        comment.refresh_from_db()
        assert comment.approved is (not start)


class TestCouponCodes:
    def test_editing_a_code(self, purged):
        code = PromotionCodeFactory()
        purged.reset()

        code.is_active = False
        code.save()

        assert "promotions" in purged()

    def test_generating_codes(self, purged):
        promotion = PromotionFactory()
        purged.reset()

        created = _create_codes(
            promotion, 3, prefix="X-", length=6, usage_limit=None
        )

        assert created == 3
        assert "promotions" in purged()


class TestPromotionWindow:
    @pytest.fixture(autouse=True)
    def _fresh_cursor(self):
        cache.delete(offer_sync._WINDOW_CURSOR_KEY)

    def test_a_window_closing_purges_the_offers(self, purged):
        now = timezone.now()
        PromotionFactory(
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(minutes=5),
        )
        purged.reset()

        offer_sync.refresh_window_changes(now)

        assert "promotions" in purged()

    def test_a_window_opening_purges_the_offers(self, purged):
        now = timezone.now()
        PromotionFactory(starts_at=now - timedelta(minutes=5))
        purged.reset()

        offer_sync.refresh_window_changes(now)

        assert "promotions" in purged()

    def test_no_edge_crossed_purges_nothing(self, purged):
        now = timezone.now()
        PromotionFactory(
            starts_at=now - timedelta(days=5),
            ends_at=now + timedelta(days=5),
        )
        purged.reset()

        offer_sync.refresh_window_changes(now)

        assert "promotions" not in purged()


class TestVatRate:
    def test_a_rate_edit_purges_the_products(self, purged):
        vat = VatFactory(value=24)
        ProductFactory(vat=vat, num_images=0, num_reviews=0)
        purged.reset()

        vat.value = 13
        vat.save()

        assert "products" in purged()
