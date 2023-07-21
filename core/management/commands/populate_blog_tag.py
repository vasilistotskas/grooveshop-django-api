# populate_blog_tag.py
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

        if total_tags < 1:
            self.stdout.write(
                self.style.WARNING("Total number of blog tags must be greater than 0.")
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        created_tags = []
        with transaction.atomic():
            for _ in range(total_tags):
                active = faker.boolean()

                # Create a new BlogTag object
                tag, created = BlogTag.objects.get_or_create(active=active)

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        name = faker.word()
                        tag.set_current_language(lang)
                        tag.name = name
                        tag.save()
                    created_tags.append(tag)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_tags)} BlogTag instances."
            )
        )
