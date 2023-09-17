# populate_blog_author.py
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
        total_authors = options["total_authors"]
        languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_authors < 1:
            self.stdout.write(
                self.style.WARNING("Total number of authors must be greater than 0.")
            )
            return

        # Get all existing user accounts
        users = list(User.objects.all())

        if not users:
            self.stdout.write(self.style.ERROR("No existing User instances found."))
            self.stdout.write(self.style.WARNING("Aborting seeding BlogAuthor model."))
            return

        # Create a list to store created authors
        created_authors = []

        with transaction.atomic():
            for _ in range(total_authors):
                # If there are no more user accounts left, break the loop to avoid duplicates
                if not users:
                    break

                # Randomly select a user account and remove it from the list to avoid duplicates
                user = faker.random_element(users)
                users.remove(user)

                # Generate random data for website and bio
                website = faker.url()[:255]  # Trim to fit URLField max length
                bio = faker.paragraph()

                # Create a new BlogAuthor object for the user
                author, created = BlogAuthor.objects.get_or_create(
                    user=user, defaults={"website": website, "bio": bio}
                )

                if created:
                    for lang in languages:
                        lang_seed = hash(f"{user.id}{lang}")
                        faker.seed_instance(lang_seed)
                        bio = faker.paragraph()
                        author.set_current_language(lang)
                        author.bio = bio
                        author.save()
                    created_authors.append(author)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_authors)} BlogAuthor instances."
            )
        )
