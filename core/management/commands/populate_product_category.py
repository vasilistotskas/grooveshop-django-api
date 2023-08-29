# populate_product_category.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.models.category import ProductCategory

faker = Faker()


class Command(BaseCommand):
    help = "Seed ProductCategory model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_categories",
            type=int,
            help="Indicates the number of categories to be seeded.",
            default=10,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_categories = options["total_categories"]

        if total_categories < 1:
            self.stdout.write(
                self.style.WARNING("Total number of categories must be greater than 0.")
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        # Create a list to store created categories
        created_categories = []

        with transaction.atomic():
            for _ in range(total_categories):
                slug = faker.slug()

                # Create a new ProductCategory object
                category, created = ProductCategory.objects.get_or_create(
                    slug=slug,
                )

                if created:
                    for lang in available_languages:
                        lang_seed = hash(f"{slug}{lang}")
                        faker.seed_instance(lang_seed)
                        name = faker.word()
                        description = faker.text()
                        category.set_current_language(lang)
                        category.name = name
                        category.description = description
                        category.save()
                    created_categories.append(category)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_categories)} ProductCategory instances."
            )
        )
