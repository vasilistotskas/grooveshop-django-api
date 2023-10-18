# populate_product.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.models.category import ProductCategory
from product.models.product import Product
from vat.models import Vat

faker = Faker()


class Command(BaseCommand):
    help = "Seed Product model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_products",
            type=int,
            help="Indicates the number of products to be seeded.",
            default=1000,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_products = options["total_products"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_products < 1:
            self.stdout.write(
                self.style.WARNING("Total number of products must be greater than 0.")
            )
            return

        categories = list(ProductCategory.objects.all())
        vats = list(Vat.objects.all())

        if not categories or not vats:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Product model.")
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_products):
                category = faker.random_element(categories)
                vat = faker.random_element(vats)
                slug = faker.unique.slug()
                price = faker.pydecimal(left_digits=4, right_digits=2, positive=True)
                stock = faker.random_int(min=0, max=1000)
                discount_percent = faker.pydecimal(
                    left_digits=2, right_digits=2, positive=True, max_value=99
                )
                weight = faker.pydecimal(left_digits=3, right_digits=2, positive=True)

                product_slug_exists = Product.objects.filter(slug=slug).exists()
                if product_slug_exists:
                    continue

                product = Product(
                    slug=slug,
                    category=category,
                    vat=vat,
                    price=price,
                    stock=stock,
                    discount_percent=discount_percent,
                    weight=weight,
                )
                objects_to_insert.append(product)
            Product.objects.bulk_create(objects_to_insert)

            for product in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{lang}{product.id}")
                    faker.seed_instance(lang_seed)

                    name = f"{faker.word()}_{product.id}"
                    description = f"{faker.text()}_{product.id}"
                    product.set_current_language(lang)
                    product.name = name
                    product.description = description
                    product.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} Product instances created successfully in {execution_time:.2f} seconds."
            )
        )
