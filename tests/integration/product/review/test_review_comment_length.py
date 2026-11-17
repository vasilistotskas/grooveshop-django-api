"""A review's comment has one length bound, on every path that writes it.

``COMMENT_MAX_LENGTH`` (``product.models.review``) is the bound: the write
serializer rejects a longer comment, the model validates it (the admin's
form and ``full_clean``), and the schema publishes it as ``maxLength`` on
the request only. A response is never checked, so a comment stored before
the bound existed is still read whole.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.review import COMMENT_MAX_LENGTH, ProductReview
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE


@pytest.fixture
def owner():
    return UserAccountFactory(num_addresses=0, is_superuser=False)


@pytest.fixture
def product():
    return ProductFactory(num_images=0, num_reviews=0)


@pytest.fixture
def api(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


def _payload(product, comment):
    return {
        "product": product.pk,
        "rate": 8,
        "translations": {LANGUAGE: {"comment": comment}},
    }


def _bought(owner, product):
    order = OrderFactory(
        user=owner, status=OrderStatus.COMPLETED, num_order_items=0
    )
    OrderItemFactory(order=order, product=product, quantity=1)


class TestTheWriteSerializer:
    def test_a_comment_at_the_bound_is_accepted(self, api, owner, product):
        _bought(owner, product)

        response = api.post(
            reverse("product-review-list"),
            data=_payload(product, "x" * COMMENT_MAX_LENGTH),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_a_longer_comment_is_refused(self, api, owner, product):
        _bought(owner, product)

        response = api.post(
            reverse("product-review-list"),
            data=_payload(product, "x" * (COMMENT_MAX_LENGTH + 1)),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ProductReview.objects.filter(user=owner).exists()

    def test_an_edit_to_a_longer_comment_is_refused(self, api, owner, product):
        review = ProductReviewFactory(
            product=product,
            user=owner,
            status=ReviewStatus.NEW,
            translations=[],
        )

        response = api.patch(
            reverse("product-review-detail", args=[review.pk]),
            data={
                "translations": {
                    LANGUAGE: {"comment": "x" * (COMMENT_MAX_LENGTH + 1)}
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTheModel:
    def test_the_form_field_carries_the_bound(self):
        field = ProductReview._parler_meta.get_model_by_field(
            "comment"
        )._meta.get_field("comment")

        assert field.max_length == COMMENT_MAX_LENGTH
        assert field.formfield().max_length == COMMENT_MAX_LENGTH

    def test_full_clean_refuses_a_longer_comment(self, owner, product):
        review = ProductReviewFactory(
            product=product, user=owner, translations=[]
        )
        review.set_current_language(LANGUAGE)
        review.comment = "x" * (COMMENT_MAX_LENGTH + 1)
        translation = review.get_translation(LANGUAGE)

        with pytest.raises(ValidationError) as raised:
            translation.full_clean(exclude=["master"])

        assert "comment" in raised.value.message_dict


class TestAStoredLongCommentStillReads:
    def test_the_detail_answers_the_whole_comment(self, api, owner, product):
        review = ProductReviewFactory(
            product=product,
            user=owner,
            status=ReviewStatus.TRUE,
            translations=[],
        )
        review.set_current_language(LANGUAGE)
        review.comment = "y" * (COMMENT_MAX_LENGTH * 3)
        review.save()

        response = api.get(reverse("product-review-detail", args=[review.pk]))

        assert response.status_code == status.HTTP_200_OK
        comment = response.json()["translations"][LANGUAGE]["comment"]
        assert len(comment) == COMMENT_MAX_LENGTH * 3
