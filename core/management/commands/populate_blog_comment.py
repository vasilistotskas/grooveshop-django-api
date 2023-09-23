# populate_blog_comment.py
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.models.comment import BlogComment
from blog.models.post import BlogPost

faker = Faker()
User = get_user_model()


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
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_comments < 1:
            self.stdout.write(
                self.style.WARNING("Total number of comments must be greater than 0.")
            )
            return

        users = list(User.objects.all())
        blog_posts = list(BlogPost.objects.all())

        if not users or not blog_posts:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding BlogComment model."
                )
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        picked_users = []
        picked_posts = []
        with transaction.atomic():
            for _ in range(total_comments):
                user = faker.random_element(users)
                blog_post = faker.random_element(blog_posts)
                is_approved = faker.boolean()
                likes = faker.random_elements(
                    users, unique=True, length=faker.random_int(min=0, max=5)
                )

                blog_comment_exists = BlogComment.objects.filter(
                    user=user, post=blog_post
                ).exists()

                if (
                    blog_comment_exists
                    or user in picked_users
                    or blog_post in picked_posts
                ):
                    continue

                comment = BlogComment(
                    user=user,
                    post=blog_post,
                    is_approved=is_approved,
                )

                objects_to_insert.append(comment)
                picked_users.append(user)
                picked_posts.append(blog_post)
            BlogComment.objects.bulk_create(objects_to_insert)

            for comment in objects_to_insert:
                comment.likes.add(*likes)
                for lang in available_languages:
                    lang_seed = hash(
                        f"{comment.user.id}{comment.post.id}{lang}{comment.id}"
                    )
                    faker.seed_instance(lang_seed)
                    content = faker.text(max_nb_chars=1000)
                    comment.set_current_language(lang)
                    comment.content = content
                    comment.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} BlogComment instances created successfully in {execution_time:.2f} seconds."
            )
        )
