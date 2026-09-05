"""The product detail page must not publish drafts or rejected reviews.

Two surfaces on the busiest page in the storefront disagreed with the
dedicated endpoints beside them:

* `GET /api/v1/product/{pk}/reviews` did `product.reviews.all()`, while
  `ProductReviewViewSet` filtered by status. Anonymous callers were
  served reviews an admin had moderated to `FALSE` — i.e. rejected as
  spam — and reviews never approved at all.

* `GET /api/v1/product/{pk}` used `for_detail()`, whose docstring says
  outright that it "does NOT filter by active status" so staff can open
  any product by id. The view is `AllowAny`, so every unreleased draft
  was readable — name, price and SEO copy — at a sequential id.
  `for_list()` has always applied `.active()`; only the detail path was
  open. `product/admin.py`'s "duplicate product" action creates exactly
  such drafts, describing them as out of the storefront.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _results(response):
    data = response.data
    return (
        data["results"]
        if isinstance(data, dict) and "results" in data
        else data
    )


@pytest.fixture
def product_with_moderated_reviews():
    product = ProductFactory(active=True)
    reviews = {
        state: ProductReviewFactory(
            product=product, user=UserAccountFactory(), status=state
        )
        for state in (ReviewStatus.TRUE, ReviewStatus.FALSE, ReviewStatus.NEW)
    }
    return product, reviews


def test_the_detail_page_does_not_publish_rejected_or_pending_reviews(
    product_with_moderated_reviews,
):
    product, reviews = product_with_moderated_reviews

    response = APIClient().get(
        reverse("product-reviews", kwargs={"pk": product.pk})
    )

    assert response.status_code == status.HTTP_200_OK
    returned = {row["id"] for row in _results(response)}
    assert returned == {reviews[ReviewStatus.TRUE].pk}, (
        "the detail page served reviews the dedicated endpoint hides"
    )


def test_the_two_review_endpoints_agree(product_with_moderated_reviews):
    """The defect was divergence, so agreement is the property to pin."""
    product, _ = product_with_moderated_reviews
    anon = APIClient()

    from_detail_page = {
        row["id"]
        for row in _results(
            anon.get(reverse("product-reviews", kwargs={"pk": product.pk}))
        )
    }
    from_dedicated = {
        row["id"]
        for row in _results(
            anon.get(reverse("product-review-list"), {"product": product.pk})
        )
    }

    assert from_detail_page == from_dedicated


def test_an_author_still_sees_their_own_pending_review(
    product_with_moderated_reviews,
):
    """A submission awaiting moderation must not look lost to its author."""
    product, reviews = product_with_moderated_reviews
    pending = reviews[ReviewStatus.NEW]

    client = APIClient()
    client.force_authenticate(user=pending.user)
    response = client.get(reverse("product-reviews", kwargs={"pk": product.pk}))

    assert pending.pk in {row["id"] for row in _results(response)}


def test_an_unreleased_draft_is_not_readable_by_the_public():
    draft = ProductFactory(active=True)
    draft.active = False
    draft.save(update_fields=["active"])

    response = APIClient().get(
        reverse("product-detail", kwargs={"pk": draft.pk})
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_live_product_is_still_readable_by_the_public():
    live = ProductFactory(active=True)

    response = APIClient().get(
        reverse("product-detail", kwargs={"pk": live.pk})
    )

    assert response.status_code == status.HTTP_200_OK
