from random import randrange

from django.core.management import BaseCommand
from django.utils.timezone import now
from faker import Faker

from country.models import Country
from order.models import Order
from order.models import OrderItem
from pay_way.models import PayWay


class Command(BaseCommand):
    def handle(self, *args, **options):
        faker = Faker()
        user_id = randrange(1, 10)

        for _ in range(30):
            country: Country = Country.objects.get(alpha_2="GR")
            try:
                pay_way = PayWay.objects.get(name="Credit Card")
            except PayWay.DoesNotExist:
                pay_way = PayWay.objects.create(
                    name="Credit Card",
                    active=True,
                    cost=5.0,
                    free_for_order_amount=100.0,
                )
            region = country.region_alpha_2.first()
            order = Order.objects.create(
                user_id=user_id,
                first_name=faker.first_name(),
                last_name=faker.last_name(),
                email=faker.email(),
                country=country,
                region=region,
                floor=1,
                location_type=0,
                street=faker.street_name(),
                street_number=faker.building_number(),
                zipcode=faker.zipcode(),
                place=faker.text(5),
                phone=faker.phone_number(),
                mobile_phone=faker.phone_number(),
                customer_notes=faker.text(100),
                created_at=now(),
                paid_amount=10.0,
                pay_way=pay_way,
            )

            product_id = randrange(1, 399)

            for _ in range(randrange(1, 5)):
                price = randrange(10, 100)
                quantity = randrange(1, 5)
                OrderItem.objects.create(
                    order_id=order.id,
                    product_id=product_id,
                    price=price,
                    quantity=quantity,
                )

        self.stdout.write(self.style.SUCCESS("Success"))
