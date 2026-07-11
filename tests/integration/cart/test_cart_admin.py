import pytest
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from djmoney.money import Money

from cart.admin import (
    CartAdmin,
    CartItemAdmin,
    CartItemInline,
    CartTypeFilter,
    TotalItemsFilter,
    ActivityStatusFilter,
)
from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart, CartItem
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.assert_english

User = get_user_model()


@pytest.fixture
def admin_request():
    factory = RequestFactory()
    request = factory.get("/admin/cart/cart/")
    request.user = Mock()
    request.user.is_authenticated = True
    request.user.is_staff = True
    request.user.is_superuser = True
    return request


@pytest.fixture
def cart_admin():
    return CartAdmin(Cart, AdminSite())


@pytest.fixture
def cart_item_admin():
    return CartItemAdmin(CartItem, AdminSite())


@pytest.fixture
def cart_item_inline():
    return CartItemInline(CartItem, AdminSite())


@pytest.mark.django_db
class TestCartTypeFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = CartTypeFilter(
            admin_request, {}, Cart, CartAdmin(Cart, AdminSite())
        )
        lookups = filter_instance.lookups(admin_request, None)

        assert len(lookups) == 3
        lookup_values = [lookup[0] for lookup in lookups]
        assert "authenticated" in lookup_values
        assert "guest" in lookup_values
        assert "abandoned" in lookup_values

    def test_filter_parameter_name(self, admin_request):
        filter_instance = CartTypeFilter(
            admin_request, {}, Cart, CartAdmin(Cart, AdminSite())
        )
        assert filter_instance.parameter_name == "cart_type"


@pytest.mark.django_db
class TestActivityStatusFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = ActivityStatusFilter(
            admin_request, {}, Cart, CartAdmin(Cart, AdminSite())
        )
        lookups = filter_instance.lookups(admin_request, None)

        assert len(lookups) == 3
        lookup_values = [lookup[0] for lookup in lookups]
        assert "active" in lookup_values
        assert "recent" in lookup_values
        assert "old" in lookup_values

    def test_filter_parameter_name(self, admin_request):
        filter_instance = ActivityStatusFilter(
            admin_request, {}, Cart, CartAdmin(Cart, AdminSite())
        )
        assert filter_instance.parameter_name == "activity_status"


@pytest.mark.django_db
class TestTotalItemsFilter:
    def test_filter_parameter_name(self, admin_request):
        filter_instance = TotalItemsFilter(
            admin_request, {}, Cart, CartAdmin(Cart, AdminSite())
        )
        assert filter_instance.parameter_name == "total_items"


@pytest.mark.django_db
class TestCartAdmin:
    def test_cart_owner_display_with_user(self, cart_admin):
        user = UserAccountFactory()
        cart = CartFactory(user=user)

        primary, secondary, _initials = cart_admin.cart_owner_display(cart)

        assert primary == (user.full_name or user.username)
        assert secondary == user.email

    def test_cart_owner_display_guest(self, cart_admin):
        cart = CartFactory(user=None)

        primary, secondary, _initials = cart_admin.cart_owner_display(cart)

        assert primary == "Guest"
        assert secondary == f"Cart #{cart.id}"

    def test_cart_type_authenticated(self, cart_admin):
        user = UserAccountFactory()
        cart = CartFactory(user=user)

        value, label = cart_admin.cart_type(cart)

        assert value == "user"
        assert label == "User"

    def test_cart_type_guest(self, cart_admin):
        cart = CartFactory(user=None)

        value, label = cart_admin.cart_type(cart)

        assert value == "guest"
        assert label == "Guest"

    def test_activity_state_active(self, cart_admin):
        recent_time = timezone.now() - timedelta(minutes=30)
        cart = CartFactory()
        Cart.objects.filter(id=cart.id).update(last_activity=recent_time)
        cart.refresh_from_db()

        value, label = cart_admin.activity_state(cart)

        assert value == "active"
        assert label == "Active"

    def test_activity_state_recent(self, cart_admin):
        recent_time = timezone.now() - timedelta(hours=2)
        cart = CartFactory()
        Cart.objects.filter(id=cart.id).update(last_activity=recent_time)
        cart.refresh_from_db()

        value, label = cart_admin.activity_state(cart)

        assert value == "recent"
        assert label == "Recent"

    def test_activity_state_idle(self, cart_admin):
        idle_time = timezone.now() - timedelta(days=3)
        cart = CartFactory()
        Cart.objects.filter(id=cart.id).update(last_activity=idle_time)
        cart.refresh_from_db()

        value, label = cart_admin.activity_state(cart)

        assert value == "idle"
        assert label == "Idle"

    def test_activity_state_abandoned(self, cart_admin):
        old_time = timezone.now() - timedelta(days=10)
        cart = CartFactory()
        Cart.objects.filter(id=cart.id).update(last_activity=old_time)
        cart.refresh_from_db()

        value, label = cart_admin.activity_state(cart)

        assert value == "abandoned"
        assert label == "Abandoned"

    def test_items_summary_empty_cart(self, cart_admin):
        cart = CartFactory()

        result = cart_admin.items_summary(cart)

        assert "0 items" in result
        assert "0 unique" in result

    def test_items_summary_with_items(self, cart_admin):
        cart = CartFactory()
        product1 = ProductFactory()
        product2 = ProductFactory()

        CartItemFactory(cart=cart, product=product1, quantity=2)
        CartItemFactory(cart=cart, product=product2, quantity=3)

        result = cart_admin.items_summary(cart)

        assert "5 items" in result
        assert "2 unique" in result

    def test_price_summary(self, cart_admin):
        cart = CartFactory()
        product = ProductFactory(price=Money(10, "EUR"))
        CartItemFactory(cart=cart, product=product, quantity=2)

        result = cart_admin.price_summary(cart)

        assert "€" in result

    def test_cart_summary(self, cart_admin):
        cart = CartFactory()

        result = cart_admin.cart_summary(cart)

        assert "Items:" in result
        assert "Total price:" in result
        assert "VAT:" in result
        assert "Last activity:" in result
        assert "Created:" in result

    def test_financial_summary(self, cart_admin):
        cart = CartFactory()

        result = cart_admin.financial_summary(cart)

        assert "Final total:" in result
        assert "VAT:" in result
        assert "Savings:" in result


@pytest.mark.django_db
class TestCartItemInline:
    def test_product_display_with_product(self, cart_item_inline):
        product = ProductFactory()
        cart_item = CartItemFactory(product=product)

        result = cart_item_inline.product_display(cart_item)

        assert str(product.id) in result

    def test_unit_price_display_with_discount(self, cart_item_inline):
        cart_item = Mock()
        cart_item.price = Money(10, "EUR")
        cart_item.final_price = Money(8, "EUR")

        result = cart_item_inline.unit_price_display(cart_item)

        assert "10" in result
        assert "8" in result

    def test_unit_price_display_without_discount(self, cart_item_inline):
        cart_item = Mock()
        cart_item.price = Money(10, "EUR")
        cart_item.final_price = Money(10, "EUR")

        result = cart_item_inline.unit_price_display(cart_item)

        assert "10" in result
        assert "→" not in result

    def test_unit_price_display_no_attributes(self, cart_item_inline):
        cart_item = Mock(spec=[])

        result = cart_item_inline.unit_price_display(cart_item)

        assert result == "-"

    def test_total_price_display(self, cart_item_inline):
        cart_item = Mock()
        cart_item.total_price = Money(20, "EUR")

        result = cart_item_inline.total_price_display(cart_item)

        assert "20" in result

    def test_total_price_display_no_attribute(self, cart_item_inline):
        cart_item = Mock(spec=[])

        result = cart_item_inline.total_price_display(cart_item)

        assert result == "-"

    def test_discount_info_with_discount(self, cart_item_inline):
        cart_item = Mock()
        cart_item.discount_percent = 15

        result = cart_item_inline.discount_info(cart_item)

        assert result == "-15%"

    def test_discount_info_without_discount(self, cart_item_inline):
        cart_item = Mock()
        cart_item.discount_percent = 0

        result = cart_item_inline.discount_info(cart_item)

        assert result == ""

    def test_discount_info_no_attribute(self, cart_item_inline):
        cart_item = Mock(spec=[])

        result = cart_item_inline.discount_info(cart_item)

        assert result == ""


@pytest.mark.django_db
class TestCartItemAdmin:
    def test_cart_info_display(self, cart_item_admin):
        user = UserAccountFactory()
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart)

        result = cart_item_admin.cart_info(item)

        expected_name = user.full_name or user.username
        assert expected_name in result
        assert f"Cart #{cart.id}" in result

    def test_product_display(self, cart_item_admin):
        product = ProductFactory()
        item = CartItemFactory(product=product)

        result = cart_item_admin.product_display(item)

        assert str(product.id) in result

    def test_quantity_display(self, cart_item_admin):
        item = CartItemFactory(quantity=5)

        result = cart_item_admin.quantity_display(item)

        assert result == "x5"

    def test_pricing_info(self, cart_item_admin):
        product = ProductFactory(price=Money(15, "EUR"))
        item = CartItemFactory(product=product, quantity=3)

        result = cart_item_admin.pricing_info(item)

        assert "€" in result

    def test_discount_display_with_discount(self, cart_item_admin):
        item = Mock()
        item.discount_percent = 20

        result = cart_item_admin.discount_display(item)

        assert result == "-20%"

    def test_discount_display_without_discount(self, cart_item_admin):
        item = Mock()
        item.discount_percent = 0

        result = cart_item_admin.discount_display(item)

        assert result == ""

    @patch.object(CartItemAdmin, "message_user")
    def test_increase_quantity_action_basic(
        self, mock_message_user, cart_item_admin, admin_request
    ):
        item = CartItemFactory(quantity=2)
        queryset = CartItem.objects.filter(id=item.id)

        cart_item_admin.increase_quantity(admin_request, queryset)

        item.refresh_from_db()
        assert item.quantity == 3
        mock_message_user.assert_called_once()

    @patch.object(CartItemAdmin, "message_user")
    def test_decrease_quantity_action_basic(
        self, mock_message_user, cart_item_admin, admin_request
    ):
        item = CartItemFactory(quantity=3)
        queryset = CartItem.objects.filter(id=item.id)

        cart_item_admin.decrease_quantity(admin_request, queryset)

        item.refresh_from_db()
        assert item.quantity == 2
        mock_message_user.assert_called_once()

    @patch.object(CartItemAdmin, "message_user")
    def test_remove_from_cart_action_basic(
        self, mock_message_user, cart_item_admin, admin_request
    ):
        item = CartItemFactory()
        item_id = item.id
        queryset = CartItem.objects.filter(id=item_id)

        cart_item_admin.remove_from_cart(admin_request, queryset)

        assert not CartItem.objects.filter(id=item_id).exists()
        mock_message_user.assert_called_once()
