# populate_blog_comment.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from user.models import UserAccount

faker = Faker()


class Command(BaseCommand):
    help = "Seed BlogComment model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_comments",
            type=int,
            help="Indicates the number of comments to be seeded.",
            default=500,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_comments = options["total_comments"]

        if total_comments < 1:
            self.stdout.write(
                self.style.WARNING("Total number of comments must be greater than 0.")
            )
            return

        # Get all existing users and blog posts
        users = list(UserAccount.objects.all())
        blog_posts = list(BlogPost.objects.all())

        if not users or not blog_posts:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding BlogComment model."
                )
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        created_comments = []
        with transaction.atomic():
            for _ in range(total_comments):
                user = faker.random_element(users)
                blog_post = faker.random_element(blog_posts)
                is_approved = faker.boolean()
                likes = faker.random_elements(
                    users, unique=True, length=faker.random_int(min=0, max=5)
                )

                # Create a new BlogComment object
                comment, created = BlogComment.objects.get_or_create(
                    user=user,
                    post=blog_post,
                    is_approved=is_approved,
                )
                comment.likes.add(*likes)

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        content = faker.text(max_nb_chars=1000)
                        comment.set_current_language(lang)
                        comment.content = content
                        comment.save()
                    created_comments.append(comment)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_comments)} BlogComment instances."
            )
        )
