# populate_blog_tag.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.tag import BlogTag

faker = Faker()


class Command(BaseCommand):
    help = "Seed BlogTag model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_tags",
            type=int,
            help="Indicates the number of blog tags to be seeded.",
            default=25,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_tags = options["total_tags"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_tags < 1:
            self.stdout.write(
                self.style.WARNING("Total number of blog tags must be greater than 0.")
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_tags):
                active = faker.boolean()

                tag = BlogTag(active=active)
                objects_to_insert.append(tag)
            BlogTag.objects.bulk_create(objects_to_insert)

            for tag in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{tag.id}{lang}")
                    faker.seed_instance(lang_seed)
                    name = faker.word()
                    tag.set_current_language(lang)
                    tag.name = name
                    tag.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} BlogTag instances created successfully in {execution_time:.2f} seconds."
            )
        )
