# populate_order.py
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from country.models import Country
from order.enum.status_enum import StatusEnum
from order.models import Order
from order.models import OrderItem
from pay_way.models import PayWay
from product.models.product import Product
from region.models import Region
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.models import UserAccount

faker = Faker()


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
        total = options["total_orders"]

        if total < 1:
            self.stdout.write(
                self.style.WARNING("Total number of orders must be greater than 0.")
            )
            return

        # Get all existing users, products, countries, and regions
        users = list(UserAccount.objects.all())
        products = list(Product.objects.all())
        countries = list(Country.objects.all())
        regions = list(Region.objects.all())
        pay_ways = list(PayWay.objects.all())

        # Get enum
        floor_choices = [choice[0] for choice in FloorChoicesEnum.choices()]
        location_choices = [choice[0] for choice in LocationChoicesEnum.choices()]
        status_choices = [choice[0] for choice in StatusEnum.choices()]

        if not users or not products or not countries or not regions or not pay_ways:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Order model.")
            )
            return

        # Create a list to store created orders
        created_orders = []

        with transaction.atomic():
            for _ in range(total):
                # Randomly select user, product, country, and region
                user = faker.random_element(users)
                product = faker.random_element(products)
                country = faker.random_element(countries)
                region = faker.random_element(regions)
                pay_way = faker.random_element(pay_ways)

                # Generate random data for other fields
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
                document_type = faker.random_element(elements=["receipt", "invoice"])

                # Create a new Order object
                order = Order.objects.create(
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

                # Create OrderItem for the Order
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    price=Decimal(faker.random_number(digits=2)),
                    quantity=faker.random_int(min=1, max=10),
                )

                created_orders.append(order)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_orders)} Order instances."
            )
        )
