"""The product admin's row and bulk actions.

Three defects, each verified by execution:

* "Duplicate as draft" set `clone.uuid = None` under a comment saying
  "regenerates". `UUIDModel.uuid` is `UUIDField(default=uuid4,
  unique=True)` with no `null=True`, and an explicit None OVERRIDES the
  default — so the INSERT sent NULL and the action always 500ed.

* "Apply custom discount" rebuilt its queryset from
  `request.session["selected_product_ids"]`, discarding the selection
  Django had already built from the POSTed `_selected_action` ids. The
  session is shared across TABS.

* Both discount actions used `queryset.update()`, which emits no
  `post_save` — so simple-history wrote no row, `product_price_lowered`
  was never sent, and not one price-drop alert reached the customers who
  subscribed to it. Measured: 0 receivers fired vs 1 for an instance
  save.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin import helpers
from django.contrib.admin.sites import AdminSite
from django.db.models.signals import post_save
from django.test import RequestFactory

from product.admin import ProductAdmin
from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def product_admin():
    return ProductAdmin(Product, AdminSite())


@pytest.fixture
def operator(db):
    """A real user: the admin actions check model permissions."""
    from user.factories.account import UserAccountFactory

    return UserAccountFactory(num_addresses=0, is_staff=True, is_superuser=True)


def _with_messages(request):
    """`duplicate_product_row` uses the module-level `messages` API,
    which needs a storage backend the RequestFactory does not attach."""
    from django.contrib.messages.storage.fallback import FallbackStorage

    request.session = getattr(request, "session", {})
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def request_factory():
    return RequestFactory()


def _make_product(**kwargs):
    return ProductFactory(num_images=0, num_reviews=0, **kwargs)


def test_duplicating_a_product_does_not_500(
    product_admin, request_factory, operator
):
    original = _make_product(active=True)
    request = _with_messages(request_factory.get("/admin/"))
    request.user = operator

    with patch.object(ProductAdmin, "message_user"):
        product_admin.duplicate_product_row(request, original.pk)

    clone = Product.objects.exclude(pk=original.pk).order_by("-pk").first()
    assert clone is not None, "no clone was created"
    assert clone.uuid is not None
    assert clone.uuid != original.uuid, "the clone reused the original's uuid"
    assert clone.active is False, "a duplicate must start as a draft"


def test_the_discount_form_uses_the_posted_selection_not_the_session(
    product_admin, request_factory, operator
):
    """The session is shared across tabs; the POST is not."""
    chosen = _make_product(active=True)
    bystander = _make_product(active=True)

    request = request_factory.post(
        "/admin/",
        {
            helpers.ACTION_CHECKBOX_NAME: [str(chosen.pk)],
            "apply": "1",
            "discount_percent": "70",
        },
    )
    request.user = operator
    request.session = {
        # What another tab left behind.
        "selected_product_ids": [chosen.pk, bystander.pk]
    }

    with patch.object(ProductAdmin, "message_user"):
        product_admin.apply_custom_discount(
            request, Product.objects.filter(pk=chosen.pk)
        )

    chosen.refresh_from_db()
    bystander.refresh_from_db()
    assert chosen.discount_percent == Decimal(70)
    assert bystander.discount_percent != Decimal(70), (
        "a product from another tab's selection was discounted"
    )


def test_applying_a_discount_fires_the_price_drop_path(
    product_admin, request_factory, operator
):
    product = _make_product(active=True, discount_percent=Decimal(0))
    fired = []

    def spy(sender, instance, **kwargs):
        fired.append(instance.pk)

    request = request_factory.post(
        "/admin/",
        {
            helpers.ACTION_CHECKBOX_NAME: [str(product.pk)],
            "apply": "1",
            "discount_percent": "40",
        },
    )
    request.user = operator
    request.session = {}

    post_save.connect(spy, sender=Product, dispatch_uid="zz_spy", weak=False)
    try:
        with patch.object(ProductAdmin, "message_user"):
            product_admin.apply_custom_discount(
                request, Product.objects.filter(pk=product.pk)
            )
    finally:
        post_save.disconnect(sender=Product, dispatch_uid="zz_spy")

    assert product.pk in fired, (
        "the discount was applied with a bulk UPDATE, so no history row, "
        "no price_lowered signal and no price-drop alert"
    )
    product.refresh_from_db()
    assert product.discount_percent == Decimal(40)


def test_clearing_a_discount_also_fires(
    product_admin, request_factory, operator
):
    product = _make_product(active=True, discount_percent=Decimal(30))
    fired = []

    def spy(sender, instance, **kwargs):
        fired.append(instance.pk)

    request = request_factory.post("/admin/")
    request.user = operator

    post_save.connect(spy, sender=Product, dispatch_uid="zz_spy2", weak=False)
    try:
        with patch.object(ProductAdmin, "message_user"):
            product_admin.clear_discount(
                request, Product.objects.filter(pk=product.pk)
            )
    finally:
        post_save.disconnect(sender=Product, dispatch_uid="zz_spy2")

    assert product.pk in fired
    product.refresh_from_db()
    assert product.discount_percent == Decimal(0)
