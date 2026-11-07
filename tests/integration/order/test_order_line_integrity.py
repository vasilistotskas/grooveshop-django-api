"""A placed order's lines are a record, not a form.

A line's product, quantity and price decide what the customer owes
(``amount_due``), what the receipt bills, the VAT lines the turnover report
reads (``OrderVatLine``, written once at checkout) and the stock that was
taken. The admin used to let staff edit a line's quantity, add a line and
delete one on any order: the post-save handler moved stock and wrote a note,
but ``amount_due`` and ``OrderVatLine`` stayed what they were, so the order,
its receipt and the turnover disagreed. A line is now created by checkout
and changed only by the services that own the change (a restock).

A line also snapshots the product's name and SKU, in the order's language,
like ``vat_rate`` and ``unit_cost``: renaming a product must not rewrite the
receipts and histories of the orders already placed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.test import Client, RequestFactory
from django.urls import reverse
from djmoney.money import Money

from order.admin import OrderAdmin, OrderItemAdmin, OrderItemInline
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.invoicing import _render_items
from order.line_snapshot import backfill_line_snapshots
from order.models.item import OrderItem, product_name_for
from order.models.order import Order
from order.models.vat_line import OrderVatLine
from order.vat_snapshot import write_vat_lines
from product.factories.product import ProductFactory
from product.models.product import Product
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db


def _paid_order() -> tuple[Order, OrderItem]:
    product = ProductFactory.create(stock=10, num_images=0, num_reviews=0)
    order = OrderFactory.create(
        num_order_items=0,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        shipping_price=Money(0, "EUR"),
    )
    item = order.items.create(
        product=product, price=Money(Decimal("20.00"), "EUR"), quantity=2
    )
    Order.objects.filter(pk=order.pk).update(
        amount_due=order.calculate_order_total_amount()
    )
    order.refresh_from_db()
    write_vat_lines(order)
    return order, item


def _request():
    request = RequestFactory().post("/")
    request.user = UserAccountFactory.create(admin=True)
    return request


class TestThePlacedOrderCannotBeEditedThroughTheAdmin:
    def test_the_inline_offers_nothing_to_edit_or_delete(self):
        order, _item = _paid_order()
        inline = OrderItemInline(Order, AdminSite())

        formset_class = inline.get_formset(_request(), order)

        assert "quantity" not in formset_class.form.base_fields
        assert not formset_class.can_delete
        assert formset_class.max_num == 0

    def test_the_inline_cannot_add_or_delete(self):
        order, _item = _paid_order()
        inline = OrderItemInline(Order, AdminSite())

        assert not inline.has_add_permission(_request(), order)
        assert not inline.has_delete_permission(_request(), order)

    def test_the_line_admin_cannot_add_or_delete(self):
        _order, item = _paid_order()
        item_admin = OrderItemAdmin(OrderItem, AdminSite())

        assert not item_admin.has_add_permission(_request())
        assert not item_admin.has_delete_permission(_request(), item)

    @pytest.mark.parametrize(
        "field",
        ["order", "product", "quantity", "price", "refunded_quantity"],
    )
    def test_the_line_admin_shows_the_record_read_only(self, field):
        _order, item = _paid_order()
        item_admin = OrderItemAdmin(OrderItem, AdminSite())

        assert field in item_admin.get_readonly_fields(_request(), item)
        assert field not in item_admin.get_form(_request(), item).base_fields

    def test_there_is_no_add_order_in_the_admin(self):
        order_admin = OrderAdmin(Order, AdminSite())

        assert not order_admin.has_add_permission(_request())


class TestALineSnapshotsTheProduct:
    def test_the_name_and_sku_are_captured_when_the_line_is_created(self):
        product = ProductFactory.create(num_images=0, num_reviews=0)
        order = OrderFactory.create(num_order_items=0)
        product.set_current_language(order.language_code)
        product.name = "Original name"
        product.save()

        item = order.items.create(
            product=product, price=Money(Decimal("5.00"), "EUR"), quantity=1
        )

        assert item.product_name == "Original name"
        assert item.sku == product.sku

    def test_renaming_the_product_leaves_the_invoice_lines_alone(self):
        order, item = _paid_order()
        original = item.product_name
        assert original

        product = Product.objects.get(pk=item.product_id)
        for code in ("el", "en"):
            product.set_current_language(code)
            product.name = "Renamed afterwards"
        product.sku = "RENAMED-SKU"
        product.save()

        [row] = _render_items(order)
        assert row["name"] == original
        assert row["sku"] == item.sku
        assert "Renamed afterwards" not in row["name"]

    def test_a_line_keeps_its_snapshot_when_saved_again(self):
        _order, item = _paid_order()
        snapshot = (item.product_name, item.sku)

        item.notes = "gift wrap"
        item.save()

        item.refresh_from_db()
        assert (item.product_name, item.sku) == snapshot


def test_the_vat_lines_belong_to_the_checkout_that_wrote_them():
    order, _item = _paid_order()

    assert OrderVatLine.objects.filter(order=order).exists()
    assert admin.site.is_registered(OrderItem)


class TestAVatRateCorrectionRewritesTheVatLines:
    """The change form of a line is the one admin edit left: a wrong rate
    may be corrected until the invoice is registered with AADE, and the
    turnover report reads ``OrderVatLine``, not the line."""

    def test_posting_a_new_rate_rewrites_the_order_vat_lines(self):
        order, item = _paid_order()
        OrderItem.objects.filter(pk=item.pk).update(vat_rate=Decimal(24))
        write_vat_lines(order)
        before = {
            row.vat_rate for row in OrderVatLine.objects.filter(order=order)
        }
        assert Decimal(24) in before
        client = Client()
        client.force_login(
            UserAccountFactory.create(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )

        response = client.post(
            reverse("admin:order_orderitem_change", args=[item.pk]),
            {"vat_rate": "13", "notes": "", "_save": "Save"},
        )

        assert response.status_code == 302, response.context["errors"]
        item.refresh_from_db()
        assert item.vat_rate == Decimal(13)
        rates = {
            row.vat_rate
            for row in OrderVatLine.objects.filter(order=order, item=item)
        }
        assert rates == {Decimal(13)}

    def test_a_form_save_that_leaves_the_rate_alone_keeps_the_vat_lines(self):
        order, item = _paid_order()
        keep = list(
            OrderVatLine.objects.filter(order=order).values_list(
                "pk", flat=True
            )
        )
        client = Client()
        client.force_login(
            UserAccountFactory.create(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )

        client.post(
            reverse("admin:order_orderitem_change", args=[item.pk]),
            {
                "vat_rate": str(item.vat_rate),
                "notes": "gift wrap",
                "_save": "Save",
            },
        )

        assert (
            list(
                OrderVatLine.objects.filter(order=order).values_list(
                    "pk", flat=True
                )
            )
            == keep
        )


class TestBackfillLineSnapshots:
    """``backfill_line_snapshots`` picks the name the way a new line does."""

    @staticmethod
    def _product(**names):
        product = ProductFactory.create(num_images=0, num_reviews=0)
        product.translations.all().delete()
        for language, name in names.items():
            product.set_current_language(language)
            product.name = name
            product.save()
        return Product.objects.get(pk=product.pk)

    @staticmethod
    def _unsnapshotted_line(product, language):
        order = OrderFactory.create(num_order_items=0, language_code=language)
        item = order.items.create(
            product=product, price=Money(Decimal("5.00"), "EUR"), quantity=1
        )
        OrderItem.objects.filter(pk=item.pk).update(product_name="", sku="")
        item.refresh_from_db()
        return item

    @pytest.mark.parametrize(
        ("names", "language"),
        [
            ({"el": "Ποδήλατο", "en": "Bike"}, "en"),
            ({"el": "Ποδήλατο", "en": "Bike"}, "el"),
            ({"en": "Bike"}, "el"),
            ({"el": "Ποδήλατο"}, "en"),
        ],
    )
    def test_the_name_is_the_one_a_new_line_would_get(self, names, language):
        product = self._product(**names)
        item = self._unsnapshotted_line(product, language)

        written = backfill_line_snapshots(connection)

        item.refresh_from_db()
        assert written == 1
        assert item.sku == product.sku
        assert item.product_name
        assert item.product_name == product_name_for(product, language)
        assert item.product_name == (
            names[language] if language in names else next(iter(names.values()))
        )

    def test_a_second_run_changes_nothing(self):
        item = self._unsnapshotted_line(
            self._product(el="Ποδήλατο", en="Bike"), "en"
        )
        assert backfill_line_snapshots(connection) == 1
        item.refresh_from_db()
        snapshot = (item.product_name, item.sku)

        assert backfill_line_snapshots(connection) == 0

        item.refresh_from_db()
        assert (item.product_name, item.sku) == snapshot

    def test_a_line_that_already_has_its_snapshot_is_not_touched(self):
        _order, item = _paid_order()
        OrderItem.objects.filter(pk=item.pk).update(product_name="Kept")

        backfill_line_snapshots(connection)

        item.refresh_from_db()
        assert item.product_name == "Kept"
