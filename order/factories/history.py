import random

import factory
from django.conf import settings
from django.contrib.auth import get_user_model
from faker import Faker

from order.enum.status import OrderStatus, PaymentStatus
from order.models.history import OrderHistory, OrderItemHistory

fake = Faker()

User = get_user_model()


def get_fake_useragent():
    browser_types = [
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(80, 120)}.0.{random.randint(1000, 9999)}.{random.randint(100, 999)} Safari/537.36",
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{random.randint(70, 110)}.0) Gecko/20100101 Firefox/{random.randint(70, 110)}.0",
        f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{random.randint(13, 15)}_{random.randint(1, 7)}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{random.randint(13, 16)}.{random.randint(0, 9)} Safari/605.1.15",
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(80, 120)}.0.{random.randint(1000, 9999)}.{random.randint(100, 999)} Edg/{random.randint(80, 120)}.0.{random.randint(100, 999)}.{random.randint(10, 99)}",
        f"Mozilla/5.0 (Linux; Android {random.randint(9, 13)}; SM-G{random.randint(900, 999)}U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.randint(80, 120)}.0.{random.randint(1000, 9999)}.{random.randint(100, 999)} Mobile Safari/537.36",
        f"Mozilla/5.0 (iPhone; CPU iPhone OS {random.randint(13, 16)}_{random.randint(0, 6)} like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{random.randint(13, 16)}.0 Mobile/15E148 Safari/604.1",
    ]
    return random.choice(browser_types)


def _existing_user():
    return User.objects.order_by("?").first() if User.objects.exists() else None


def _two_distinct(choices) -> tuple:
    old, new = random.sample([choice[0] for choice in choices], 2)
    return old, new


def _price() -> str:
    return f"${random.randint(10, 100)}.{random.randint(0, 99):02d}"


def _set_description(instance, text: str) -> None:
    for language in settings.PARLER_LANGUAGES[settings.SITE_ID]:
        instance.set_current_language(language["code"])
        instance.description = text
    instance.save()


# The values are declared, not written by a post-generation hook, so
# an explicit ``previous_value``/``new_value`` always wins; only the
# description is derived afterwards, from whatever was stored.


def _order_values(change_type: str) -> tuple:
    match change_type:
        case "STATUS":
            old, new = _two_distinct(OrderStatus.choices)
            return {"status": old}, {"status": new}
        case "PAYMENT":
            old, new = _two_distinct(PaymentStatus.choices)
            return {"payment_status": old}, {"payment_status": new}
        case "REFUND":
            return None, {"amount": _price(), "reason": fake.sentence()}
        case _:
            return None, {"note": fake.paragraph()}


def _order_description(change_type, previous, new) -> str:
    previous = previous or {}
    new = new or {}
    match change_type:
        case "STATUS":
            return (
                f"Status changed from {previous.get('status')} "
                f"to {new.get('status')}"
            )
        case "PAYMENT":
            return (
                f"Payment status updated from "
                f"{previous.get('payment_status')} "
                f"to {new.get('payment_status')}"
            )
        case "NOTE":
            return "Note added to order"
        case "REFUND":
            return f"Refund processed for {new.get('amount')}"
        case _:
            return fake.sentence()


class OrderHistoryFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory("order.factories.order.OrderFactory")
    user = factory.LazyFunction(_existing_user)
    change_type = factory.Iterator(
        [choice[0] for choice in OrderHistory.OrderHistoryChangeType.choices]
    )
    previous_value = factory.LazyAttribute(lambda o: o.values[0])
    new_value = factory.LazyAttribute(lambda o: o.values[1])
    ip_address = factory.Faker("ipv4")
    user_agent = factory.LazyFunction(get_fake_useragent)

    class Params:
        values = factory.LazyAttribute(lambda o: _order_values(o.change_type))

    class Meta:
        model = OrderHistory
        skip_postgeneration_save = True

    @factory.post_generation
    def description(self, create, extracted, **kwargs):
        if create:
            _set_description(
                self,
                extracted
                or _order_description(
                    self.change_type, self.previous_value, self.new_value
                ),
            )

    @classmethod
    def create_status_change(
        cls, order=None, old_status=None, new_status=None, **kwargs
    ):
        if old_status is None or new_status is None:
            old_status, new_status = _two_distinct(OrderStatus.choices)
        return cls.create(
            **({"order": order} if order is not None else {}),
            change_type="STATUS",
            previous_value={"status": old_status},
            new_value={"status": new_status},
            **kwargs,
        )

    @classmethod
    def create_payment_update(
        cls,
        order=None,
        old_payment_status=None,
        new_payment_status=None,
        **kwargs,
    ):
        if old_payment_status is None or new_payment_status is None:
            old_payment_status, new_payment_status = _two_distinct(
                PaymentStatus.choices
            )
        return cls.create(
            **({"order": order} if order is not None else {}),
            change_type="PAYMENT",
            previous_value={"payment_status": old_payment_status},
            new_value={"payment_status": new_payment_status},
            **kwargs,
        )

    @classmethod
    def create_note(cls, order=None, note=None, **kwargs):
        return cls.create(
            **({"order": order} if order is not None else {}),
            change_type="NOTE",
            previous_value=None,
            new_value={"note": note or fake.paragraph()},
            **kwargs,
        )

    @classmethod
    def create_for_order(cls, order, count=None, **kwargs):
        if count is None:
            count = random.randint(1, 5)
        return cls.create_batch(count, order=order, **kwargs)


def _item_values(change_type: str) -> tuple:
    match change_type:
        case "PRICE":
            return {"price": _price()}, {"price": _price()}
        case "REFUND":
            return (
                {"refunded_quantity": 0},
                {"refunded_quantity": random.randint(1, 5)},
            )
        case _:
            old, new = random.sample(range(1, 6), 2)
            return {"quantity": old}, {"quantity": new}


def _item_description(change_type, previous, new) -> str:
    previous = previous or {}
    new = new or {}
    match change_type:
        case "QUANTITY":
            return (
                f"Quantity changed from {previous.get('quantity')} "
                f"to {new.get('quantity')}"
            )
        case "PRICE":
            return (
                f"Price updated from {previous.get('price')} "
                f"to {new.get('price')}"
            )
        case "REFUND":
            return f"Refunded {new.get('refunded_quantity')} units"
        case _:
            return fake.sentence()


class OrderItemHistoryFactory(factory.django.DjangoModelFactory):
    order_item = factory.SubFactory("order.factories.item.OrderItemFactory")
    user = factory.LazyFunction(_existing_user)
    change_type = factory.Iterator(
        [
            choice[0]
            for choice in OrderItemHistory.OrderItemHistoryChangeType.choices
        ]
    )
    previous_value = factory.LazyAttribute(lambda o: o.values[0])
    new_value = factory.LazyAttribute(lambda o: o.values[1])

    class Params:
        values = factory.LazyAttribute(lambda o: _item_values(o.change_type))

    class Meta:
        model = OrderItemHistory
        skip_postgeneration_save = True

    @factory.post_generation
    def description(self, create, extracted, **kwargs):
        if create:
            _set_description(
                self,
                extracted
                or _item_description(
                    self.change_type, self.previous_value, self.new_value
                ),
            )

    @classmethod
    def create_quantity_change(
        cls, order_item=None, old_quantity=None, new_quantity=None, **kwargs
    ):
        if old_quantity is None or new_quantity is None:
            old_quantity, new_quantity = random.sample(range(1, 6), 2)
        return cls.create(
            **({"order_item": order_item} if order_item is not None else {}),
            change_type="QUANTITY",
            previous_value={"quantity": old_quantity},
            new_value={"quantity": new_quantity},
            **kwargs,
        )

    @classmethod
    def create_price_update(
        cls, order_item=None, old_price=None, new_price=None, **kwargs
    ):
        return cls.create(
            **({"order_item": order_item} if order_item is not None else {}),
            change_type="PRICE",
            previous_value={"price": old_price or _price()},
            new_value={"price": new_price or _price()},
            **kwargs,
        )

    @classmethod
    def create_refund(cls, order_item=None, refund_quantity=None, **kwargs):
        if not refund_quantity:
            refund_quantity = random.randint(
                1, order_item.quantity if order_item else 5
            )
        return cls.create(
            **({"order_item": order_item} if order_item is not None else {}),
            change_type="REFUND",
            previous_value={"refunded_quantity": 0},
            new_value={"refunded_quantity": refund_quantity},
            **kwargs,
        )

    @classmethod
    def create_for_order_item(cls, order_item, count=None, **kwargs):
        if count is None:
            count = random.randint(1, 3)
        return cls.create_batch(count, order_item=order_item, **kwargs)
