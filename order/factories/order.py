import importlib
import random
from datetime import timedelta

import factory
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone
from djmoney.money import Money
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum, PaymentStatusEnum
from order.factories.item import OrderItemFactory
from order.models.order import Order
from user.enum.address import FloorChoicesEnum, LocationChoicesEnum

fake = Faker()

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = (
            User.objects.annotate(num_orders=Count("orders"))
            .order_by("num_orders")
            .first()
        )
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


def get_or_create_country():
    if apps.get_model("country", "Country").objects.exists():
        return (
            apps.get_model("country", "Country").objects.order_by("?").first()
        )
    else:
        country_factory_module = importlib.import_module("country.factories")
        country_factory_class = country_factory_module.CountryFactory
        return country_factory_class.create()


def get_or_create_region():
    if apps.get_model("region", "Region").objects.exists():
        return apps.get_model("region", "Region").objects.order_by("?").first()
    else:
        region_factory_module = importlib.import_module("region.factories")
        region_factory_class = region_factory_module.RegionFactory
        return region_factory_class.create()


def get_or_create_pay_way():
    if apps.get_model("pay_way", "PayWay").objects.exists():
        return apps.get_model("pay_way", "PayWay").objects.order_by("?").first()
    else:
        pay_way_factory_module = importlib.import_module("pay_way.factories")
        pay_way_factory_class = pay_way_factory_module.PayWayFactory
        return pay_way_factory_class.create()


def generate_tracking_number():
    """Generate a realistic tracking number."""
    carriers = ["FEDEX", "UPS", "USPS", "DHL"]
    carrier = random.choice(carriers)

    if carrier == "FEDEX":
        return f"FEDEX-{fake.numerify('###########')}"
    elif carrier == "UPS":
        return f"1Z{fake.bothify('???#########')}"
    elif carrier == "USPS":
        return f"9{fake.numerify('############')}US"
    elif carrier == "DHL":
        return f"DHL-{fake.numerify('##########')}"
    return None


def generate_payment_data(status=None):
    """Generate realistic payment data based on status."""
    payment_methods = ["CREDIT_CARD", "PAYPAL", "BANK_TRANSFER", "STRIPE"]
    payment_method = random.choice(payment_methods)

    if payment_method == "CREDIT_CARD":
        payment_id = f"CC-{fake.numerify('###-###-###')}"
    elif payment_method == "PAYPAL":
        payment_id = f"PP-{fake.numerify('##########')}"
    elif payment_method == "BANK_TRANSFER":
        payment_id = f"BT-{fake.numerify('######')}"
    elif payment_method == "STRIPE":
        payment_id = f"pi_{fake.lexify('?' * 24)}"

    # Default to a random payment status if not specified
    if not status:
        if random.randint(1, 10) > 2:  # 80% chance of being completed
            status = PaymentStatusEnum.COMPLETED
        else:
            status = random.choice(
                [
                    PaymentStatusEnum.PENDING,
                    PaymentStatusEnum.PROCESSING,
                    PaymentStatusEnum.FAILED,
                ]
            )

    return {
        "payment_id": payment_id,
        "payment_method": payment_method,
        "payment_status": status,
    }


class OrderFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    country = factory.LazyFunction(get_or_create_country)
    region = factory.LazyFunction(get_or_create_region)
    pay_way = factory.LazyFunction(get_or_create_pay_way)
    floor = factory.Iterator(FloorChoicesEnum.choices, getter=lambda x: x[0])
    location_type = factory.Iterator(
        LocationChoicesEnum.choices, getter=lambda x: x[0]
    )
    email = factory.Faker("email")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    street = factory.Faker("street_name")
    street_number = factory.Faker("building_number")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    place = factory.LazyFunction(
        lambda: fake.secondary_address() if random.randint(1, 10) > 5 else ""
    )
    phone = factory.LazyAttribute(
        lambda _: PhoneNumber.from_string(fake.phone_number(), region="US")
    )
    mobile_phone = factory.LazyAttribute(
        lambda _: PhoneNumber.from_string(fake.phone_number(), region="US")
        if random.randint(1, 10) > 3
        else None
    )
    customer_notes = factory.LazyFunction(
        lambda: fake.paragraph(nb_sentences=2)
        if random.randint(1, 10) > 7
        else ""
    )
    status = factory.LazyFunction(
        lambda: random.choice([s[0] for s in OrderStatusEnum.choices])
    )
    shipping_price = factory.LazyFunction(
        lambda: Money(
            fake.pydecimal(
                left_digits=2,
                right_digits=2,
                min_value=5,
                max_value=20,
                positive=True,
            ),
            "USD",
        )
    )
    document_type = factory.Iterator(
        OrderDocumentTypeEnum.choices, getter=lambda x: x[0]
    )
    paid_amount = factory.LazyFunction(
        lambda: Money(
            fake.pydecimal(
                left_digits=3,
                right_digits=2,
                min_value=10,
                max_value=99,
                positive=True,
            ),
            "USD",
        )
        if random.randint(1, 10) > 3
        else Money(0, "USD")
    )
    payment_status = factory.LazyFunction(
        lambda: random.choice([s[0] for s in PaymentStatusEnum.choices])
    )
    payment_id = factory.LazyFunction(
        lambda: f"payment_{fake.uuid4()}" if random.randint(1, 10) > 3 else ""
    )
    payment_method = factory.LazyFunction(
        lambda: random.choice(
            ["CREDIT_CARD", "PAYPAL", "BANK_TRANSFER", "STRIPE"]
        )
        if random.randint(1, 10) > 3
        else ""
    )
    tracking_number = factory.LazyFunction(
        lambda: generate_tracking_number() if random.randint(1, 10) > 5 else ""
    )
    shipping_carrier = factory.LazyFunction(
        lambda: random.choice(["FEDEX", "UPS", "USPS", "DHL"])
        if random.randint(1, 10) > 5
        else ""
    )
    status_updated_at = factory.LazyFunction(
        lambda: timezone.now() - timedelta(days=random.randint(0, 30))
        if random.randint(1, 10) > 3
        else None
    )

    class Meta:
        model = Order
        django_get_or_create = ("user",)
        skip_postgeneration_save = True

    @factory.post_generation
    def num_order_items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            OrderItemFactory.create_batch(extracted, order=self)
        else:
            # By default, create 1-5 items
            count = random.randint(1, 5)
            OrderItemFactory.create_batch_for_order(order=self, count=count)

    @classmethod
    def create_with_consistent_status_data(cls, status=None, **kwargs):
        """
        Create an order with data that is consistent with its status.

        This ensures that orders in specific states have appropriate data for that state,
        such as tracking numbers for shipped orders and payment data for paid orders.
        """
        if status is None:
            status = random.choice([s[0] for s in OrderStatusEnum.choices])

        now = timezone.now()
        data = {"status": status, "status_updated_at": now, **kwargs}

        # Set appropriate payment data based on status
        if status in [
            OrderStatusEnum.COMPLETED,
            OrderStatusEnum.SHIPPED,
            OrderStatusEnum.DELIVERED,
        ]:
            payment_data = generate_payment_data(PaymentStatusEnum.COMPLETED)
            data.update(payment_data)
            data["paid_amount"] = Money(
                fake.pydecimal(
                    left_digits=3,
                    right_digits=2,
                    min_value=10,
                    max_value=99,
                    positive=True,
                ),
                "USD",
            )
        elif status == OrderStatusEnum.CANCELED:
            # For canceled orders, payment may have failed or been canceled
            payment_status = random.choice(
                [
                    PaymentStatusEnum.FAILED,
                    PaymentStatusEnum.CANCELED,
                    "",  # Sometimes no payment was attempted
                ]
            )
            if payment_status:
                payment_data = generate_payment_data(payment_status)
                data.update(payment_data)
        elif status == OrderStatusEnum.REFUNDED:
            # Refunded orders must have been paid first
            payment_data = generate_payment_data(PaymentStatusEnum.REFUNDED)
            data.update(payment_data)
            data["paid_amount"] = Money(
                fake.pydecimal(
                    left_digits=3,
                    right_digits=2,
                    min_value=10,
                    max_value=99,
                    positive=True,
                ),
                "USD",
            )

        # Add tracking information for shipped/delivered orders
        if status in [OrderStatusEnum.SHIPPED, OrderStatusEnum.DELIVERED]:
            tracking_number = generate_tracking_number()
            carrier = (
                tracking_number.split("-")[0]
                if "-" in tracking_number
                else random.choice(["FEDEX", "UPS", "USPS", "DHL"])
            )
            data["tracking_number"] = tracking_number
            data["shipping_carrier"] = carrier

        # Create the order and its items
        order = cls.create(**data)
        return order

    @classmethod
    def create_pending_order(cls, **kwargs):
        """Create a pending order awaiting payment."""
        return cls.create_with_consistent_status_data(
            status=OrderStatusEnum.PENDING,
            payment_status=PaymentStatusEnum.PENDING,
            **kwargs,
        )

    @classmethod
    def create_processing_order(cls, **kwargs):
        """Create an order being processed (payment complete, preparing for shipping)."""
        return cls.create_with_consistent_status_data(
            status=OrderStatusEnum.PROCESSING,
            payment_status=PaymentStatusEnum.COMPLETED,
            **kwargs,
        )

    @classmethod
    def create_shipped_order(cls, **kwargs):
        """Create an order that has been shipped with tracking info."""
        return cls.create_with_consistent_status_data(
            status=OrderStatusEnum.SHIPPED, **kwargs
        )

    @classmethod
    def create_completed_order(cls, **kwargs):
        """Create a completed order (delivered and finalized)."""
        return cls.create_with_consistent_status_data(
            status=OrderStatusEnum.COMPLETED, **kwargs
        )

    @classmethod
    def create_canceled_order(cls, **kwargs):
        """Create a canceled order."""
        return cls.create_with_consistent_status_data(
            status=OrderStatusEnum.CANCELED, **kwargs
        )

    @classmethod
    def create_refunded_order(cls, **kwargs):
        """Create a refunded order with associated refunded items."""
        order = cls.create_with_consistent_status_data(
            status=OrderStatusEnum.REFUNDED,
            payment_status=PaymentStatusEnum.REFUNDED,
            **kwargs,
        )

        # If the order has items, mark some or all as refunded
        if hasattr(order, "items") and order.items.exists():
            items = list(order.items.all())
            # Select a subset of items to refund (or all of them)
            refund_all = random.choice([True, False])
            items_to_refund = (
                items
                if refund_all
                else random.sample(items, random.randint(1, len(items)))
            )

            for item in items_to_refund:
                # Either full or partial refund
                full_refund = random.choice([True, False])
                if full_refund or item.quantity == 1:
                    refund_qty = item.quantity
                else:
                    # Handle the case where quantity is 1
                    refund_qty = random.randint(1, max(1, item.quantity - 1))

                item.refunded_quantity = refund_qty
                item.is_refunded = refund_qty == item.quantity
                item.save()

        return order
