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


class OrderHistoryFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory("order.factories.order.OrderFactory")
    user = factory.LazyFunction(
        lambda: User.objects.order_by("?").first()
        if User.objects.exists()
        else None
    )
    change_type = factory.Iterator(
        [choice[0] for choice in OrderHistory.OrderHistoryChangeType.choices]
    )
    previous_value = factory.LazyFunction(
        lambda: {
            "status": random.choice(
                [choice[0] for choice in OrderStatus.choices]
            )
        }
        if random.choice([True, False])
        else None
    )
    new_value = factory.LazyFunction(
        lambda: {
            "status": random.choice(
                [choice[0] for choice in OrderStatus.choices]
            )
        }
        if random.choice([True, False])
        else {"note": fake.sentence()}
    )
    ip_address = factory.Faker("ipv4")
    user_agent = factory.LazyFunction(get_fake_useragent)

    class Meta:
        model = OrderHistory
        skip_postgeneration_save = True

    @factory.post_generation
    def set_translatable_fields(self, create, extracted, **kwargs):
        if not create:
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        description_text = fake.sentence()

        for lang_code in available_languages:
            self.set_current_language(lang_code)
            self.description = description_text

    @factory.post_generation
    def set_change_type_specific_data(self, create, extracted, **kwargs):
        if not create:
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        description_text = ""

        if self.change_type == "STATUS":
            old_status = random.choice(
                [choice[0] for choice in OrderStatus.choices]
            )
            new_status = random.choice(
                [choice[0] for choice in OrderStatus.choices]
            )
            while new_status == old_status:
                new_status = random.choice(
                    [choice[0] for choice in OrderStatus.choices]
                )

            self.previous_value = {"status": old_status}
            self.new_value = {"status": new_status}
            description_text = (
                f"Status changed from {old_status} to {new_status}"
            )

        elif self.change_type == "PAYMENT":
            old_payment_status = random.choice(
                [choice[0] for choice in PaymentStatus.choices]
            )
            new_payment_status = random.choice(
                [choice[0] for choice in PaymentStatus.choices]
            )

            self.previous_value = {"payment_status": old_payment_status}
            self.new_value = {"payment_status": new_payment_status}
            description_text = f"Payment status updated from {old_payment_status} to {new_payment_status}"

        elif self.change_type == "NOTE":
            self.previous_value = None
            self.new_value = {"note": fake.paragraph()}
            description_text = "Note added to order"

        elif self.change_type == "REFUND":
            amount = f"${random.randint(5, 100)}.{random.randint(0, 99):02d}"
            self.new_value = {"amount": amount, "reason": fake.sentence()}
            description_text = f"Refund processed for {amount}"

        for lang_code in available_languages:
            self.set_current_language(lang_code)
            self.description = description_text

        self.save()

    @classmethod
    def create_status_change(cls, order=None, **kwargs):
        old_status = kwargs.pop(
            "old_status",
            random.choice([choice[0] for choice in OrderStatus.choices]),
        )
        new_status = kwargs.pop(
            "new_status",
            random.choice([choice[0] for choice in OrderStatus.choices]),
        )

        while new_status == old_status:
            new_status = random.choice(
                [choice[0] for choice in OrderStatus.choices]
            )

        instance = cls.create(
            order=order,
            change_type="STATUS",
            previous_value={"status": old_status},
            new_value={"status": new_status},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = f"Status changed from {old_status} to {new_status}"

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_payment_update(cls, order=None, **kwargs):
        old_payment_status = kwargs.pop(
            "old_payment_status",
            random.choice([choice[0] for choice in PaymentStatus.choices]),
        )
        new_payment_status = kwargs.pop(
            "new_payment_status",
            random.choice([choice[0] for choice in PaymentStatus.choices]),
        )

        instance = cls.create(
            order=order,
            change_type="PAYMENT",
            previous_value={"payment_status": old_payment_status},
            new_value={"payment_status": new_payment_status},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = f"Payment status updated from {old_payment_status} to {new_payment_status}"

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_note(cls, order=None, note=None, **kwargs):
        if not note:
            note = fake.paragraph()

        instance = cls.create(
            order=order,
            change_type="NOTE",
            previous_value=None,
            new_value={"note": note},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = "Note added to order"

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_for_order(cls, order, count=None, **kwargs):
        if count is None:
            count = random.randint(1, 5)

        entries = []
        for _ in range(count):
            entries.append(cls.create(order=order, **kwargs))

        return entries


class OrderItemHistoryFactory(factory.django.DjangoModelFactory):
    order_item = factory.SubFactory("order.factories.item.OrderItemFactory")
    user = factory.LazyFunction(
        lambda: User.objects.order_by("?").first()
        if User.objects.exists()
        else None
    )
    change_type = factory.Iterator(
        [
            choice[0]
            for choice in OrderItemHistory.OrderItemHistoryChangeType.choices
        ]
    )
    previous_value = factory.LazyFunction(
        lambda: {"quantity": random.randint(1, 5)}
        if random.choice([True, False])
        else None
    )
    new_value = factory.LazyFunction(
        lambda: {"quantity": random.randint(1, 5)}
        if random.choice([True, False])
        else {
            "price": f"${random.randint(10, 100)}.{random.randint(0, 99):02d}"
        }
    )

    class Meta:
        model = OrderItemHistory
        skip_postgeneration_save = True

    @factory.post_generation
    def set_translatable_fields(self, create, extracted, **kwargs):
        if not create:
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        description_text = fake.sentence()

        for lang_code in available_languages:
            self.set_current_language(lang_code)
            self.description = description_text

    @factory.post_generation
    def set_change_type_specific_data(self, create, extracted, **kwargs):
        if not create:
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        description_text = ""

        if self.change_type == "QUANTITY":
            old_quantity = random.randint(1, 5)
            new_quantity = random.randint(1, 5)
            while new_quantity == old_quantity:
                new_quantity = random.randint(1, 5)

            self.previous_value = {"quantity": old_quantity}
            self.new_value = {"quantity": new_quantity}
            description_text = (
                f"Quantity changed from {old_quantity} to {new_quantity}"
            )

        elif self.change_type == "PRICE":
            old_price = (
                f"${random.randint(10, 100)}.{random.randint(0, 99):02d}"
            )
            new_price = (
                f"${random.randint(10, 100)}.{random.randint(0, 99):02d}"
            )

            self.previous_value = {"price": old_price}
            self.new_value = {"price": new_price}
            description_text = f"Price updated from {old_price} to {new_price}"

        elif self.change_type == "REFUND":
            refund_quantity = random.randint(1, 5)
            self.previous_value = {"refunded_quantity": 0}
            self.new_value = {"refunded_quantity": refund_quantity}
            description_text = f"Refunded {refund_quantity} units"

        for lang_code in available_languages:
            self.set_current_language(lang_code)
            self.description = description_text

        self.save()

    @classmethod
    def create_quantity_change(cls, order_item=None, **kwargs):
        old_quantity = kwargs.pop("old_quantity", random.randint(1, 5))
        new_quantity = kwargs.pop("new_quantity", random.randint(1, 5))

        while new_quantity == old_quantity:
            new_quantity = random.randint(1, 5)

        instance = cls.create(
            order_item=order_item,
            change_type="QUANTITY",
            previous_value={"quantity": old_quantity},
            new_value={"quantity": new_quantity},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = (
            f"Quantity changed from {old_quantity} to {new_quantity}"
        )

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_price_update(cls, order_item=None, **kwargs):
        old_price = kwargs.pop(
            "old_price",
            f"${random.randint(10, 100)}.{random.randint(0, 99):02d}",
        )
        new_price = kwargs.pop(
            "new_price",
            f"${random.randint(10, 100)}.{random.randint(0, 99):02d}",
        )

        instance = cls.create(
            order_item=order_item,
            change_type="PRICE",
            previous_value={"price": old_price},
            new_value={"price": new_price},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = f"Price updated from {old_price} to {new_price}"

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_refund(cls, order_item=None, refund_quantity=None, **kwargs):
        if not refund_quantity:
            if order_item:
                refund_quantity = random.randint(1, order_item.quantity)
            else:
                refund_quantity = random.randint(1, 5)

        instance = cls.create(
            order_item=order_item,
            change_type="REFUND",
            previous_value={"refunded_quantity": 0},
            new_value={"refunded_quantity": refund_quantity},
            **kwargs,
        )

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        description_text = f"Refunded {refund_quantity} units"

        for lang_code in available_languages:
            instance.set_current_language(lang_code)
            instance.description = description_text
        instance.save()

        return instance

    @classmethod
    def create_for_order_item(cls, order_item, count=None, **kwargs):
        if count is None:
            count = random.randint(1, 3)

        entries = []
        for _ in range(count):
            entries.append(cls.create(order_item=order_item, **kwargs))

        return entries
