"""The staff "Restock returned items" action on an order.

Returned units could only be put back on the shelf through
``POST order-items/<id>/restock``; the admin had no way to do it. The
action drives the same ``OrderItem.refund`` (no money, row lock, the
``refunded_quantity`` ceiling) from a dialog, for several lines at once
and all-or-nothing, with the staff member on the stock log.

A dialog action is driven with real POST data, not a stub form (see
``test_admin_refund_action``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from djmoney.money import Money

from order.admin import OrderAdmin, RestockReturnedForm
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.models.item import OrderItem
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.models.stock_log import StockLog
from product.factories.product import ProductFactory
from product.models.variant_group import ProductVariantGroup
from tests.utils.staff import stamp_platform_identity
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def staff_user():
    return stamp_platform_identity(
        UserAccountFactory(is_staff=True, is_superuser=True)
    )


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


def _post(user, data):
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


def _product(stock=10):
    return ProductFactory.create(
        stock=stock,
        price=Money(Decimal("20.00"), "EUR"),
        num_images=0,
        num_reviews=0,
    )


def _order(status=OrderStatus.DELIVERED):
    return OrderFactory.create(num_order_items=0, status=status)


def _item(order, product, quantity=5, **kwargs):
    return OrderItemFactory.create(
        order=order,
        product=product,
        price=Money(Decimal("20.00"), "EUR"),
        quantity=quantity,
        refunded_quantity=0,
        is_refunded=False,
        **kwargs,
    )


def _stock(product) -> int:
    product.refresh_from_db()
    return product.stock


def _restock(order_admin, user, order, data):
    request = _post(user, data)
    response = order_admin.restock_returned(request, object_id=order.pk)
    return request, response


class TestRegistration:
    def test_it_is_a_detail_action(self, order_admin):
        assert "restock_returned" in order_admin.actions_detail


class TestRestocking:
    def test_returned_units_go_back_and_are_recorded(
        self, order_admin, staff_user
    ):
        order, product = _order(), _product(stock=10)
        item = _item(order, product, quantity=5)

        request, response = _restock(
            order_admin,
            staff_user,
            order,
            {f"quantity_{item.pk}": "2", "reason": "Customer sent 2 back"},
        )

        assert response.headers["HX-Redirect"].endswith(
            f"/order/order/{order.pk}/change/"
        )
        assert _stock(product) == 12
        item.refresh_from_db()
        assert item.refunded_quantity == 2
        assert item.is_refunded is False
        assert _messages(request) == [
            f"Order {order.pk}: 2 units put back in stock."
        ]

    def test_the_stock_log_says_who_when_and_why(self, order_admin, staff_user):
        order, product = _order(), _product(stock=10)
        item = _item(order, product)

        _restock(
            order_admin,
            staff_user,
            order,
            {f"quantity_{item.pk}": "3", "reason": "Unopened return"},
        )

        [row] = StockLog.objects.filter(
            order_id=order.pk, operation_type=StockLog.OPERATION_INCREMENT
        )
        assert row.performed_by_staff_id == staff_user.pk
        assert row.product_id == product.pk
        assert row.quantity_delta == 3
        assert (row.stock_before, row.stock_after) == (10, 13)
        assert "Unopened return" in row.reason
        assert row.created_at is not None

    def test_the_admin_log_names_the_staff_member(
        self, order_admin, staff_user
    ):
        order, product = _order(), _product()
        item = _item(order, product)

        _restock(
            order_admin,
            staff_user,
            order,
            {f"quantity_{item.pk}": "1", "reason": "Wrong size"},
        )

        entry = LogEntry.objects.get(object_id=str(order.pk))
        assert entry.user_id == staff_user.pk
        assert "Wrong size" in entry.change_message

    def test_it_moves_no_money(self, order_admin, staff_user):
        order, product = _order(), _product()
        order.payment_status = PaymentStatus.COMPLETED
        order.save(update_fields=["payment_status"])
        PaymentTransactionFactory(
            order=order, amount=Money(Decimal("100.00"), "EUR")
        )
        item = _item(order, product)
        ledger_before = list(
            PaymentTransaction.objects.filter(order=order).values_list(
                "pk", flat=True
            )
        )

        _restock(
            order_admin,
            staff_user,
            order,
            {f"quantity_{item.pk}": "5", "reason": "All back"},
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert (
            list(
                PaymentTransaction.objects.filter(order=order).values_list(
                    "pk", flat=True
                )
            )
            == ledger_before
        )
        assert not order.refund_attempts.exists()

    def test_each_variant_goes_back_into_its_own_product(
        self, order_admin, staff_user
    ):
        group = ProductVariantGroup.objects.create()
        red, blue = _product(stock=4), _product(stock=7)
        for variant in (red, blue):
            variant.variant_group = group
            variant.save(update_fields=["variant_group"])
        order = _order()
        red_line = _item(order, red, quantity=3)
        blue_line = _item(order, blue, quantity=3)

        _restock(
            order_admin,
            staff_user,
            order,
            {
                f"quantity_{red_line.pk}": "2",
                f"quantity_{blue_line.pk}": "0",
                "reason": "Red came back",
            },
        )

        assert _stock(red) == 6
        assert _stock(blue) == 7

    def test_several_lines_go_back_together(self, order_admin, staff_user):
        order = _order()
        first, second = _product(stock=1), _product(stock=2)
        a, b = _item(order, first), _item(order, second)

        _restock(
            order_admin,
            staff_user,
            order,
            {
                f"quantity_{a.pk}": "1",
                f"quantity_{b.pk}": "2",
                "reason": "Both back",
            },
        )

        assert (_stock(first), _stock(second)) == (2, 4)


class TestNeverMoreThanReturned:
    def test_a_second_pass_cannot_restock_the_same_units(
        self, order_admin, staff_user
    ):
        order, product = _order(), _product(stock=10)
        item = _item(order, product, quantity=2)
        data = {f"quantity_{item.pk}": "2", "reason": "Back"}
        _restock(order_admin, staff_user, order, data)

        # Fully restocked: the action is no longer offered...
        assert not order_admin.has_restock_permission(
            _post(staff_user, {}), order.pk
        )
        # ...and the dialog refuses to run, so nothing is added twice.
        with pytest.raises(PermissionDenied):
            _restock(order_admin, staff_user, order, data)
        assert _stock(product) == 12

    def test_the_dialog_caps_a_line_at_what_is_left(self, staff_user):
        order, product = _order(), _product()
        item = _item(order, product, quantity=5)
        item.refund(3)

        form = RestockReturnedForm(
            _post(staff_user, {}),
            order.pk,
            data={
                "_form_submitted": "true",
                f"quantity_{item.pk}": "3",
                "reason": "Too many",
            },
        )

        assert not form.is_valid()
        assert f"quantity_{item.pk}" in form.errors

    def test_a_line_with_nothing_left_is_not_offered(self, staff_user):
        order, product = _order(), _product()
        done = _item(order, product, quantity=2)
        done.refund(2)
        open_line = _item(order, _product(), quantity=2)

        form = RestockReturnedForm(_post(staff_user, {}), order.pk)

        assert f"quantity_{done.pk}" not in form.fields
        assert f"quantity_{open_line.pk}" in form.fields

    def test_at_least_one_unit_must_be_entered(self, staff_user):
        order, product = _order(), _product()
        item = _item(order, product)

        form = RestockReturnedForm(
            _post(staff_user, {}),
            order.pk,
            data={
                "_form_submitted": "true",
                f"quantity_{item.pk}": "0",
                "reason": "Nothing",
            },
        )

        assert not form.is_valid()

    def test_a_stale_dialog_that_loses_the_race_restocks_nothing(
        self, order_admin, staff_user
    ):
        """The cap is checked again under the line's lock; when the second
        line fails there, the first line's units do not stay restocked."""
        order = _order()
        first, second = _product(stock=5), _product(stock=5)
        a, b = _item(order, first), _item(order, second)
        real_refund = OrderItem.refund
        calls = []

        def refund(item, quantity=None, **kwargs):
            calls.append(item.pk)
            if len(calls) == 2:
                raise ValidationError("Cannot refund more than ordered")
            return real_refund(item, quantity, **kwargs)

        with patch.object(OrderItem, "refund", refund):
            request, _response = _restock(
                order_admin,
                staff_user,
                order,
                {
                    f"quantity_{a.pk}": "1",
                    f"quantity_{b.pk}": "1",
                    "reason": "Race",
                },
            )

        assert (_stock(first), _stock(second)) == (5, 5)
        assert not StockLog.objects.filter(
            order_id=order.pk, operation_type=StockLog.OPERATION_INCREMENT
        ).exists()
        assert any("Cannot refund more" in m for m in _messages(request))


class TestStatusAndPermission:
    @pytest.mark.parametrize(
        "status",
        [OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.CANCELED],
    )
    def test_not_offered_before_delivery_or_after_a_cancel(
        self, order_admin, staff_user, status
    ):
        """A cancel restores stock itself; offering this too would put the
        same units back twice."""
        order = _order(status)
        _item(order, _product())

        assert not order_admin.has_restock_permission(
            _post(staff_user, {}), order.pk
        )

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.DELIVERED, OrderStatus.COMPLETED, OrderStatus.RETURNED],
    )
    def test_offered_while_staff_handle_a_return(
        self, order_admin, staff_user, status
    ):
        order = _order(status)
        _item(order, _product())

        assert order_admin.has_restock_permission(
            _post(staff_user, {}), order.pk
        )

    def test_the_status_is_judged_again_under_the_order_lock(
        self, order_admin, staff_user
    ):
        order, product = _order(OrderStatus.CANCELED), _product(stock=3)
        item = _item(order, product)

        with patch.object(
            OrderAdmin, "has_restock_permission", return_value=True
        ):
            request, _response = _restock(
                order_admin,
                staff_user,
                order,
                {f"quantity_{item.pk}": "1", "reason": "Late"},
            )

        assert _stock(product) == 3
        assert any(
            "delivered, completed or returned" in m for m in _messages(request)
        )

    def test_a_user_without_the_line_permission_is_refused(self, order_admin):
        order, product = _order(), _product(stock=3)
        item = _item(order, product)
        plain = UserAccountFactory(is_staff=True, is_superuser=False)

        with pytest.raises(PermissionDenied):
            _restock(
                order_admin,
                plain,
                order,
                {f"quantity_{item.pk}": "1", "reason": "No"},
            )

        assert _stock(product) == 3


class TestRefundOffersRestock:
    def test_a_refund_points_at_the_restock_action(
        self, order_admin, staff_user
    ):
        from pay_way.factories import PayWayFactory
        from tests.utils.stripe_refunds import FakeStripeRefunds

        order = OrderFactory(
            num_order_items=0,
            pay_way=PayWayFactory.create_online_payment(),
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_restock",
            amount_due=Money(Decimal("100.00"), "EUR"),
        )
        PaymentTransactionFactory(
            order=order,
            amount=Money(Decimal("100.00"), "EUR"),
            provider_reference="pi_restock",
        )
        _item(order, _product())
        request = _post(staff_user, {"amount": "20.00", "reason": "Return"})

        with FakeStripeRefunds():
            order_admin.refund_payment(request, object_id=order.pk)

        assert any("Restock returned items" in m for m in _messages(request))
        # The refund alone did not touch the shelf.
        assert not StockLog.objects.filter(
            order_id=order.pk, operation_type=StockLog.OPERATION_INCREMENT
        ).exists()
