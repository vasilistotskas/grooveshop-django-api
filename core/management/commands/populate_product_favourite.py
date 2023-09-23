# populate_product_favourite.py
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.models.favourite import ProductFavourite
from product.models.product import Product

faker = Faker()
User = get_user_model()


class Command(BaseCommand):
    help = "Seed ProductFavourite model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_favourites",
            type=int,
            help="Indicates the number of favourites to be seeded.",
            default=500,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_favourites = options["total_favourites"]
        total_time = 0
        start_time = time.time()

        if total_favourites < 1:
            self.stdout.write(
                self.style.WARNING("Total number of favourites must be greater than 0.")
            )
            return

        users = list(User.objects.all())
        products = list(Product.objects.all())

        if not users or not products:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding ProductFavourite model."
                )
            )
            return

        objects_to_insert = []
        picked_users = []
        picked_products = []
        with transaction.atomic():
            for _ in range(total_favourites):
                user = faker.random_element(users)
                product = faker.random_element(products)

                product_favourite_exists = ProductFavourite.objects.filter(
                    user=user, product=product
                ).exists()

                if (
                    product_favourite_exists
                    or user in picked_users
                    or product in picked_products
                ):
                    continue

                favourite = ProductFavourite(
                    user=user,
                    product=product,
                )

                objects_to_insert.append(favourite)
                picked_users.append(user)
                picked_products.append(product)
            ProductFavourite.objects.bulk_create(objects_to_insert)

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} ProductFavourite instances created "
                f"successfully in {execution_time:.2f} seconds."
            )
        )
