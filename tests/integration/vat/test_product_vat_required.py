"""A product always has a VAT rate, and a rate in use cannot be deleted.

``Product.vat`` used to be ``null=True, on_delete=SET_NULL``. Deleting a rate
left its products without one, and a product without one is priced, indexed,
snapshotted onto its order lines and invoiced at 0%: a silently wrong Greek
invoice. The write API and the admin accepted such a product as well.

What is pinned here is every WRITE path and the delete: nothing the code does
can create a product without a rate, or take the rate from a product that has
one. The database's own refusal (NOT NULL, ``product`` 0070) is pinned in
``test_vat_database_invariants``.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.db.models import ProtectedError
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient

from order.factories.order import OrderFactory
from order.models.item import OrderItem
from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from product.models.product import Product
from product.serializers.product import (
    ProductSerializer,
    ProductWriteSerializer,
)
from tenant.models import TenantMembershipRole
from tests.integration.texts.conftest import staff_client
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory
from vat.models import Vat

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def vat():
    return Vat.objects.get_or_create(value=D("24.0"))[0]


@pytest.fixture
def category():
    return ProductCategoryFactory()


@pytest.fixture
def tenant():
    tenant = store_tenant("vat_required")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def _payload(category, **extra) -> dict:
    return {
        "slug": "no-vat-product",
        "category": category.pk,
        "translations": json.dumps(
            {"el": {"name": "Προϊόν", "description": "Περιγραφή"}}
        ),
        "price": "100.00",
        "active": True,
        "stock": 10,
        "discount_percent": 0,
        **extra,
    }


def _staff_api() -> APIClient:
    client = APIClient()
    client.force_authenticate(
        user=UserAccountFactory(
            num_addresses=0, is_superuser=True, is_staff=True
        )
    )
    return client


class TestDeletingARateInUse:
    """The reproduction: SET_NULL took the rate from every product on it."""

    def test_the_delete_is_refused_and_the_product_keeps_its_rate(
        self, vat, category
    ):
        product = ProductFactory(
            category=category, vat=vat, price=D("100.00"), discount_percent=0
        )

        with pytest.raises(ProtectedError):
            vat.delete()

        product.refresh_from_db()
        assert product.vat_id == vat.pk
        assert product.final_price == Money("124.00", "EUR")

    def test_an_order_line_keeps_the_products_rate(self, vat, category):
        product = ProductFactory(category=category, vat=vat)
        with pytest.raises(ProtectedError):
            vat.delete()

        order = OrderFactory(num_order_items=0)
        item = OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(D("5.00"), "EUR"),
            quantity=1,
        )

        item.refresh_from_db()
        assert item.vat_rate == D("24.0")

    def test_a_rate_no_product_uses_can_still_be_deleted(self):
        unused = Vat.objects.create(value=D("9.0"))

        unused.delete()

        assert not Vat.objects.filter(pk=unused.pk).exists()

    def test_a_queryset_delete_is_refused_too(self, vat, category):
        ProductFactory(category=category, vat=vat)

        with pytest.raises(ProtectedError):
            Vat.objects.filter(pk=vat.pk).delete()

        assert Vat.objects.filter(pk=vat.pk).exists()


class TestTheWriteApiRequiresARate:
    def test_a_create_without_vat_is_refused(self, category):
        response = _staff_api().post(
            reverse("product-list"), _payload(category), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "vat" in response.json()
        assert not Product.objects.filter(slug="no-vat-product").exists()

    def test_a_create_with_a_null_vat_is_refused(self, category):
        response = _staff_api().post(
            reverse("product-list"),
            _payload(category, vat=None),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "vat" in response.json()

    def test_a_create_with_a_rate_is_accepted(self, category, vat):
        response = _staff_api().post(
            reverse("product-list"),
            _payload(category, vat=vat.pk),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Product.objects.get(slug="no-vat-product").vat_id == vat.pk

    def test_a_full_update_cannot_clear_the_rate(self, category, vat):
        product = ProductFactory(category=category, vat=vat)

        response = _staff_api().put(
            reverse("product-detail", kwargs={"pk": product.pk}),
            _payload(category, slug=product.slug, vat=None),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        product.refresh_from_db()
        assert product.vat_id == vat.pk

    def test_a_partial_update_leaves_the_rate_alone(self, category, vat):
        product = ProductFactory(category=category, vat=vat)

        response = _staff_api().patch(
            reverse("product-detail", kwargs={"pk": product.pk}),
            {"stock": 3},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        product.refresh_from_db()
        assert product.vat_id == vat.pk
        assert product.stock == 3

    def test_a_partial_update_cannot_clear_the_rate(self, category, vat):
        product = ProductFactory(category=category, vat=vat)

        response = _staff_api().patch(
            reverse("product-detail", kwargs={"pk": product.pk}),
            {"vat": None},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        product.refresh_from_db()
        assert product.vat_id == vat.pk

    def test_vat_is_required_and_not_nullable_on_both_serializers(self):
        for serializer_class in (ProductSerializer, ProductWriteSerializer):
            field = serializer_class().fields["vat"]
            assert field.required, serializer_class.__name__
            assert not field.allow_null, serializer_class.__name__


class TestTheAdminRequiresARate:
    def test_the_model_field_is_required_in_a_form(self):
        assert Product._meta.get_field("vat").blank is False

    def test_the_add_form_rejects_a_product_without_one(self, tenant):
        client, _ = staff_client(tenant, TenantMembershipRole.OWNER)

        response = client.post(reverse("admin:product_product_add"), {})

        assert response.status_code == 200
        assert "vat" in response.context["adminform"].form.errors


class TestDeletingARateInTheAdmin:
    """A rate in use is refused cleanly: the page says why, no 500."""

    def _delete_url(self, vat) -> str:
        return reverse("admin:vat_vat_delete", args=[vat.pk])

    def test_the_delete_page_lists_the_products_and_offers_no_confirm(
        self, tenant, vat, category
    ):
        client, _ = staff_client(tenant, TenantMembershipRole.OWNER)
        ProductFactory(category=category, vat=vat)

        response = client.get(self._delete_url(vat))

        assert response.status_code == 200
        assert len(response.context["protected"]) == 1
        assert 'name="post"' not in response.content.decode()

    def test_posting_the_delete_deletes_nothing(self, tenant, vat, category):
        client, _ = staff_client(tenant, TenantMembershipRole.OWNER)
        product = ProductFactory(category=category, vat=vat)

        response = client.post(self._delete_url(vat), {"post": "yes"})

        assert response.status_code == 200
        assert Vat.objects.filter(pk=vat.pk).exists()
        product.refresh_from_db()
        assert product.vat_id == vat.pk

    def test_the_bulk_delete_action_deletes_nothing(
        self, tenant, vat, category
    ):
        client, _ = staff_client(tenant, TenantMembershipRole.OWNER)
        ProductFactory(category=category, vat=vat)
        unused = Vat.objects.create(value=D("9.0"))

        response = client.post(
            reverse("admin:vat_vat_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [vat.pk, unused.pk],
                "post": "yes",
            },
        )

        assert response.status_code == 200
        assert Vat.objects.filter(pk__in=[vat.pk, unused.pk]).count() == 2

    def test_an_unused_rate_is_deleted(self, tenant):
        client, _ = staff_client(tenant, TenantMembershipRole.OWNER)
        unused = Vat.objects.create(value=D("9.0"))

        response = client.post(self._delete_url(unused), {"post": "yes"})

        assert response.status_code == 302
        assert not Vat.objects.filter(pk=unused.pk).exists()


class TestTheStandardRate:
    def test_it_is_myDATA_category_one(self):
        from order.mydata.builder import _VAT_CATEGORY_BY_RATE
        from order.mydata.types import VAT_CATEGORY_24
        from vat.constants import STANDARD_VAT_RATE

        assert _VAT_CATEGORY_BY_RATE[STANDARD_VAT_RATE] == VAT_CATEGORY_24

    def test_the_migration_names_the_same_rate(self):
        import importlib

        from vat.constants import STANDARD_VAT_RATE

        module = importlib.import_module(
            "product.migrations.0068_backfill_missing_vat"
        )

        assert module.STANDARD_VAT_RATE == STANDARD_VAT_RATE
