# populate_blog_author.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.author import BlogAuthor
from user.models import UserAccount

faker = Faker()


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
        total = options["total_authors"]
        languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total < 1:
            self.stdout.write(
                self.style.WARNING("Total number of authors must be greater than 0.")
            )
            return

        # Get all existing user accounts
        user_accounts = list(UserAccount.objects.all())

        if not user_accounts:
            self.stdout.write(
                self.style.ERROR("No existing UserAccount instances found.")
            )
            self.stdout.write(self.style.WARNING("Aborting seeding BlogAuthor model."))
            return

        # Create a list to store created authors
        created_authors = []

        with transaction.atomic():
            for _ in range(total):
                # If there are no more user accounts left, break the loop to avoid duplicates
                if not user_accounts:
                    break

                # Randomly select a user account and remove it from the list to avoid duplicates
                user = faker.random_element(user_accounts)
                user_accounts.remove(user)

                # Generate random data for website and bio
                website = faker.url()[:255]  # Trim to fit URLField max length
                bio = faker.paragraph()

                # Create a new BlogAuthor object for the user
                author, created = BlogAuthor.objects.get_or_create(
                    user=user, defaults={"website": website, "bio": bio}
                )

                if created:
                    for lang in languages:
                        faker.seed_instance(
                            lang
                        )  # Seed Faker instance for each language
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
