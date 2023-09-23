# populate_blog_post.py
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blog.enum.blog_post_enum import PostStatusEnum
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag

faker = Faker()
User = get_user_model()


class Command(BaseCommand):
    help = "Seed BlogPost model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_posts",
            type=int,
            help="Indicates the number of blog posts to be seeded.",
            default=100,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_posts = options["total_posts"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_posts < 1:
            self.stdout.write(
                self.style.WARNING("Total number of blog posts must be greater than 0.")
            )
            return

        categories = list(BlogCategory.objects.all())
        tags = list(BlogTag.objects.all())
        authors = list(BlogAuthor.objects.all())
        users = list(User.objects.all())

        if not categories or not tags or not authors or not users:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding BlogPost model.")
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        status_choices = [choice[0] for choice in PostStatusEnum.choices]

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_posts):
                slug = faker.unique.slug()
                category = faker.random_element(categories)
                tags_count = min(faker.random_int(min=1, max=5), len(tags))
                tags_list = faker.random_elements(tags, unique=True, length=tags_count)
                author = faker.random_element(authors)
                status = faker.random_element(status_choices)
                featured = faker.boolean()
                view_count = faker.random_int(min=0, max=1000)

                likes_count = faker.random_int(min=0, max=min(10, len(users)))
                likes = faker.random_elements(users, unique=True, length=likes_count)

                blog_post_exists = BlogPost.objects.filter(slug=slug).exists()
                if blog_post_exists:
                    continue

                post = BlogPost(
                    category=category,
                    author=author,
                    status=status,
                    featured=featured,
                    view_count=view_count,
                    slug=slug,
                )
                objects_to_insert.append(post)
            BlogPost.objects.bulk_create(objects_to_insert)

            for post in objects_to_insert:
                post.tags.add(*tags_list)
                post.likes.add(*likes)
                for lang in available_languages:
                    lang_seed = hash(f"{post.id}{lang}")
                    faker.seed_instance(lang_seed)
                    title = faker.sentence(nb_words=5)
                    subtitle = faker.sentence(nb_words=10)
                    body = faker.paragraph(nb_sentences=10)
                    post.set_current_language(lang)
                    post.title = title
                    post.subtitle = subtitle
                    post.body = body
                    post.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} BlogPost instances created successfully in {execution_time:.2f} seconds."
            )
        )
