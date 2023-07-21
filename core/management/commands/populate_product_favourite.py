# populate_product_favourite.py
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

        if total_favourites < 1:
            self.stdout.write(
                self.style.WARNING("Total number of favourites must be greater than 0.")
            )
            return

        # Get all existing users and products
        users = list(User.objects.all())
        products = list(Product.objects.all())

        if not users or not products:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding ProductFavourite model."
                )
            )
            return

        created_favourites = []
        with transaction.atomic():
            for _ in range(total_favourites):
                user = faker.random_element(users)
                product = faker.random_element(products)

                # Create a new ProductFavourite object
                favourite, created = ProductFavourite.objects.get_or_create(
                    user=user,
                    product=product,
                )

                if created:
                    created_favourites.append(favourite)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_favourites)} ProductFavourite instances."
            )
        )
