# populate_blog_category.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.category import BlogCategory
from helpers.seed import get_or_create_default_image

faker = Faker()


class Command(BaseCommand):
    help = "Seed BlogCategory model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_categories",
            type=int,
            help="Indicates the number of categories to be seeded.",
            default=10,
            nargs="?",
        )

    def handle(self, *args, **options):
        total = options["total_categories"]

        if total < 1:
            self.stdout.write(
                self.style.WARNING("Total number of categories must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/blog/no_photo.jpg")

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        # Create a list to store created categories
        created_categories = []

        with transaction.atomic():
            for _ in range(total):
                slug = faker.slug()

                # Create a new BlogCategory object
                category, created = BlogCategory.objects.get_or_create(
                    slug=slug,
                    image=img,
                )

                if created:
                    for lang in available_languages:
                        faker.seed_instance(
                            lang
                        )  # Seed Faker instance for each language
                        name = faker.word()
                        description = faker.text()
                        category.set_current_language(lang)
                        category.name = name
                        category.description = description
                        category.save()
                    created_categories.append(category)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_categories)} BlogCategory instances."
            )
        )
