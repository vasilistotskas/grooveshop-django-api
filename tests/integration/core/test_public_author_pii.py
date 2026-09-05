"""No anonymous endpoint may publish a customer's contact record.

`UserDetailsSerializer` is the ACCOUNT serializer: it carries `email`,
`phone`, `address`, `city`, `zipcode`, `birth_date` and the privilege
flags. It was nested as the `user` field on product reviews, blog
comments (including parent and ancestor comments) and blog authors —
every one of which serves anonymous readers.

So an unauthenticated `GET /api/v1/product/review` returned, per review,
a complete contact record for the customer who wrote it; the blog-author
route did the same for store personnel, whose home address and phone
were then public. `read_only_fields` does not help — it stops a field
being written, not rendered.

This walks the real endpoints anonymously and fails on any field a
byline has no business carrying, so a future serializer swap cannot
quietly reintroduce it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

# Rendered on a public byline: identity, not contact details.
ALLOWED = {"id", "username", "firstName", "lastName", "mainImagePath"}

# Any of these reaching an anonymous caller is the defect.
FORBIDDEN = {
    "email",
    "phone",
    "address",
    "city",
    "zipcode",
    "place",
    "birthDate",
    "isStaff",
    "isSuperuser",
    "isActive",
    "uuid",
    "country",
    "region",
}


def _rows(payload):
    return (
        payload.get("results", payload)
        if isinstance(payload, dict)
        else payload
    )


def _assert_clean(author: dict, where: str):
    leaked = sorted(set(author) & FORBIDDEN)
    assert not leaked, f"{where} published {leaked} to an anonymous caller"
    unexpected = sorted(set(author) - ALLOWED)
    assert not unexpected, (
        f"{where} exposes {unexpected}, which is not part of a public "
        f"byline — add it to ALLOWED deliberately, or stop rendering it"
    )


def test_product_reviews_do_not_publish_the_reviewer_s_contact_record():
    from product.factories.product import ProductFactory
    from product.factories.review import ProductReviewFactory
    from user.factories.account import UserAccountFactory

    reviewer = UserAccountFactory(
        email="reviewer@example.com",
        city="Athens",
        zipcode="11111",
        address="1 Real Street",
        num_addresses=0,
    )
    product = ProductFactory(num_images=0, num_reviews=0)
    ProductReviewFactory(user=reviewer, product=product, status="TRUE")

    response = APIClient().get(reverse("product-review-list"))
    assert response.status_code == 200
    rows = _rows(response.json())
    assert rows, "no reviews returned — the test proves nothing"
    _assert_clean(rows[0]["user"], "product review")


def test_blog_comments_do_not_publish_the_commenter_s_contact_record():
    from blog.factories.comment import BlogCommentFactory
    from blog.factories.post import BlogPostFactory
    from user.factories.account import UserAccountFactory

    commenter = UserAccountFactory(
        email="commenter@example.com", num_addresses=0
    )
    post = BlogPostFactory(num_comments=0)
    BlogCommentFactory(user=commenter, post=post, approved=True)

    response = APIClient().get(reverse("blog-comment-list"))
    assert response.status_code == 200
    rows = _rows(response.json())
    assert rows, "no comments returned — the test proves nothing"
    _assert_clean(rows[0]["user"], "blog comment")


def test_blog_authors_do_not_publish_store_personnel_contact_details():
    from blog.factories.author import BlogAuthorFactory
    from user.factories.account import UserAccountFactory

    staff = UserAccountFactory(
        email="editor@example.com",
        address="9 Staff Road",
        num_addresses=0,
    )
    author = BlogAuthorFactory(user=staff)

    # The LIST serializer returns `user` as a bare pk; only the DETAIL
    # serializer nests the author identity, so that is the surface that
    # could leak.
    response = APIClient().get(reverse("blog-author-detail", args=[author.id]))
    assert response.status_code == 200, response.status_code
    _assert_clean(response.json()["user"], "blog author detail")
