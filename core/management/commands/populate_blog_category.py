# populate_blog_category.py
import time

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
        total_time = 0
        start_time = time.time()
        total_categories = options["total_categories"]
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_categories < 1:
            self.stdout.write(
                self.style.WARNING("Total number of categories must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/blog/no_photo.jpg")

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_categories):
                slug = faker.unique.slug()
                category = BlogCategory(
                    slug=slug,
                    image=img,
                )
                objects_to_insert.append(category)
            BlogCategory.objects.bulk_create(objects_to_insert)

            for category in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{slug}{lang}")
                    faker.seed_instance(lang_seed)
                    name = faker.word()
                    description = faker.text()
                    category.set_current_language(lang)
                    category.name = name
                    category.description = description
                    category.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} BlogCategory instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
