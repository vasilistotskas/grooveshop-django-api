# populate_order.py
import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from country.models import Country
from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum
from order.models.item import OrderItem
from order.models.order import Order
from pay_way.models import PayWay
from product.models.product import Product
from region.models import Region
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum

faker = Faker()
User = get_user_model()


class Command(BaseCommand):
    help = "Seed Order model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_orders",
            type=int,
            help="Indicates the number of orders to be seeded.",
            default=500,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_orders = options["total_orders"]
        total_time = 0
        start_time = time.time()

        if total_orders < 1:
            self.stdout.write(
                self.style.WARNING("Total number of orders must be greater than 0.")
            )
            return

        users = list(User.objects.all())
        products = list(Product.objects.all())
        countries = list(Country.objects.all())
        regions = list(Region.objects.all())
        pay_ways = list(PayWay.objects.all())

        floor_choices = [choice[0] for choice in FloorChoicesEnum.choices]
        location_choices = [choice[0] for choice in LocationChoicesEnum.choices]
        status_choices = [choice[0] for choice in OrderStatusEnum.choices]
        document_type_choices = [choice[0] for choice in OrderDocumentTypeEnum.choices]

        if not users or not products or not countries or not regions or not pay_ways:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Order model.")
            )
            return

        order_objects_to_insert = []
        order_item_objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_orders):
                user = faker.random_element(users)
                product = faker.random_element(products)
                country = faker.random_element(countries)
                region = faker.random_element(regions)
                pay_way = faker.random_element(pay_ways)

                floor = faker.random_element(floor_choices)
                location_type = faker.random_element(location_choices)
                email = faker.email()
                first_name = faker.first_name()
                last_name = faker.last_name()
                street = faker.street_name()
                street_number = faker.random_number(digits=3)
                city = faker.city()
                zipcode = faker.zipcode()
                place = faker.random_element(elements=[None, "A", "B", "C", "D"])
                phone = faker.phone_number()
                mobile_phone = (
                    faker.phone_number()
                    if faker.boolean(chance_of_getting_true=30)
                    else None
                )
                paid_amount = Decimal(faker.random_number(digits=2))
                customer_notes = faker.text(max_nb_chars=200)
                status = faker.random_element(status_choices)
                shipping_price = Decimal(faker.random_number(digits=2))
                document_type = faker.random_element(document_type_choices)

                order = Order(
                    user=user,
                    country=country,
                    region=region,
                    pay_way=pay_way,
                    floor=floor,
                    location_type=location_type,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    street=street,
                    street_number=street_number,
                    city=city,
                    zipcode=zipcode,
                    place=place,
                    phone=phone,
                    mobile_phone=mobile_phone,
                    paid_amount=paid_amount,
                    customer_notes=customer_notes,
                    status=status,
                    shipping_price=shipping_price,
                    document_type=document_type,
                )
                order_objects_to_insert.append(order)

                order_item = OrderItem(
                    order=order,
                    product=product,
                    price=Decimal(faker.random_number(digits=2)),
                    quantity=faker.random_int(min=1, max=10),
                )
                order_item_objects_to_insert.append(order_item)

            Order.objects.bulk_create(order_objects_to_insert)
            OrderItem.objects.bulk_create(order_item_objects_to_insert)

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(order_objects_to_insert)} Order instances and"
                f" {len(order_item_objects_to_insert)} OrderItem instances created"
                f" successfully in {execution_time:.2f} seconds."
            )
        )
