# populate_product.py
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
            default=500,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_products = options["total_products"]

        if total_products < 1:
            self.stdout.write(
                self.style.WARNING("Total number of products must be greater than 0.")
            )
            return

        # Get all existing categories and vats
        categories = list(ProductCategory.objects.all())
        vats = list(Vat.objects.all())

        if not categories or not vats:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Product model.")
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        created_products = []
        with transaction.atomic():
            for _ in range(total_products):
                category = faker.random_element(categories)
                vat = faker.random_element(vats)
                slug = None

                # Ensure the slug value is unique for each product
                while not slug or Product.objects.filter(slug=slug).exists():
                    slug = faker.slug()

                price = faker.pydecimal(left_digits=4, right_digits=2, positive=True)
                stock = faker.random_int(min=0, max=1000)
                discount_percent = faker.pydecimal(
                    left_digits=2, right_digits=2, positive=True, max_value=99
                )
                weight = faker.pydecimal(left_digits=3, right_digits=2, positive=True)

                product, created = Product.objects.get_or_create(
                    slug=slug,
                    category=category,
                    vat=vat,
                    price=price,
                    stock=stock,
                    discount_percent=discount_percent,
                    weight=weight,
                )

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        name = faker.word()
                        description = faker.text()
                        product.set_current_language(lang)
                        product.name = name
                        product.description = description
                        product.save()
                    created_products.append(product)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_products)} Product instances."
            )
        )
