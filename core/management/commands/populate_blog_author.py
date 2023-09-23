# populate_blog_author.py
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.author import BlogAuthor

faker = Faker()
User = get_user_model()


class Command(BaseCommand):
    help = "Seed BlogAuthor model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_authors",
            type=int,
            help="Indicates the number of authors to be seeded.",
            default=10,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_time = 0
        start_time = time.time()
        total_authors = options["total_authors"]
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_authors < 1:
            self.stdout.write(
                self.style.WARNING("Total number of authors must be greater than 0.")
            )
            return

        users = User.objects.all()

        if not users:
            self.stdout.write(self.style.ERROR("No existing User instances found."))
            self.stdout.write(self.style.WARNING("Aborting seeding BlogAuthor model."))
            return

        objects_to_insert = []
        picked_users = []
        with transaction.atomic():
            for _ in range(total_authors):
                user = faker.random_element(users)
                website = faker.url()[:255]
                bio = faker.paragraph()

                user_exists = BlogAuthor.objects.filter(user=user).exists()
                if user_exists or user in picked_users:
                    continue

                author = BlogAuthor(user=user, website=website, bio=bio)
                objects_to_insert.append(author)
                picked_users.append(user)
            BlogAuthor.objects.bulk_create(objects_to_insert)

            for author in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{author.user.id}{lang}")
                    faker.seed_instance(lang_seed)
                    bio = faker.paragraph()
                    author.set_current_language(lang)
                    author.bio = bio
                    author.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} BlogAuthor instances created successfully in {execution_time:.2f} seconds."
            )
        )
