import pytest
import uuid
from order.factories import OrderFactory


@pytest.mark.django_db
class TestGuestOrderUUIDs:
    """
    For any order created without an authenticated user (user_id = None),
    the Order.uuid field SHALL contain a valid UUID4.
    """

    @pytest.mark.parametrize("order_count", [1, 3, 5, 10])
    def test_guest_orders_have_valid_uuids(self, order_count):
        """
        Test that guest orders (user_id=None) have valid UUID4 values.
        """
        # Create multiple guest orders
        guest_orders = [
            OrderFactory(user=None, num_order_items=1)
            for _ in range(order_count)
        ]

        for order in guest_orders:
            # Verify user_id is None (guest order)
            assert order.user_id is None, (
                f"Order {order.id} should be a guest order (user_id=None)"
            )

            # Verify uuid field is populated
            assert order.uuid is not None, (
                f"Guest order {order.id} must have a UUID"
            )

            # Verify uuid is a valid UUID4
            try:
                uuid_obj = uuid.UUID(str(order.uuid), version=4)
                assert uuid_obj.version == 4, (
                    f"Order {order.id} UUID must be version 4, got version {uuid_obj.version}"
                )
            except (ValueError, AttributeError) as e:
                pytest.fail(
                    f"Order {order.id} has invalid UUID format: {order.uuid}. Error: {e}"
                )

    def test_guest_order_uuids_are_unique(self):
        """
        Test that each guest order has a unique UUID.
        """
        # Create multiple guest orders
        guest_orders = [
            OrderFactory(user=None, num_order_items=1) for _ in range(20)
        ]

        # Collect all UUIDs
        uuids = [str(order.uuid) for order in guest_orders]

        # Verify all UUIDs are unique
        assert len(uuids) == len(set(uuids)), "Guest order UUIDs must be unique"

    def test_authenticated_orders_also_have_uuids(self):
        """
        Test that authenticated user orders also have UUIDs (not just guest orders).
        """
        # Create order with authenticated user
        order = OrderFactory(num_order_items=1)

        # Verify user is set
        assert order.user_id is not None, (
            "Order should have an authenticated user"
        )

        # Verify uuid is still populated (all orders should have UUIDs)
        assert order.uuid is not None, (
            "Authenticated order should also have a UUID"
        )

        # Verify it's a valid UUID4
        try:
            uuid_obj = uuid.UUID(str(order.uuid), version=4)
            assert uuid_obj.version == 4
        except (ValueError, AttributeError) as e:
            pytest.fail(f"Invalid UUID format: {order.uuid}. Error: {e}")

    @pytest.mark.parametrize(
        "scenario",
        [
            "guest_order",
            "authenticated_order",
        ],
    )
    def test_uuid_format_is_valid_for_all_orders(self, scenario):
        """
        Test that UUID format is valid for both guest and authenticated orders.
        """
        if scenario == "guest_order":
            order = OrderFactory(user=None, num_order_items=1)
        else:
            order = OrderFactory(num_order_items=1)

        # Verify UUID is valid
        assert order.uuid is not None

        # Verify UUID format (should be string representation of UUID4)
        uuid_str = str(order.uuid)
        assert len(uuid_str) == 36, (
            f"UUID string should be 36 characters, got {len(uuid_str)}"
        )
        assert uuid_str.count("-") == 4, "UUID should have 4 hyphens"

        # Verify it's a valid UUID4
        try:
            uuid_obj = uuid.UUID(uuid_str, version=4)
            assert uuid_obj.version == 4
        except (ValueError, AttributeError) as e:
            pytest.fail(f"Invalid UUID: {uuid_str}. Error: {e}")
