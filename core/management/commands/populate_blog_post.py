# populate_blog_post.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from faker import Faker

from blog.enum.blog_post_enum import PostStatusEnum
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from user.models import UserAccount

faker = Faker()


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

        if total_posts < 1:
            self.stdout.write(
                self.style.WARNING("Total number of blog posts must be greater than 0.")
            )
            return

        # Get all existing categories, tags, authors, and users
        categories = list(BlogCategory.objects.all())
        tags = list(BlogTag.objects.all())
        authors = list(BlogAuthor.objects.all())
        users = list(UserAccount.objects.all())

        if not categories or not tags or not authors or not users:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding BlogPost model.")
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        status_choices = [choice[0] for choice in PostStatusEnum.choices()]

        created_posts = []
        with transaction.atomic():
            for _ in range(total_posts):
                category = faker.random_element(categories)
                tags_count = min(
                    faker.random_int(min=1, max=5), len(tags)
                )  # Ensure it doesn't exceed available tags
                tags_list = faker.random_elements(tags, unique=True, length=tags_count)
                author = faker.random_element(authors)
                status = faker.random_element(status_choices)
                featured = faker.boolean()
                view_count = faker.random_int(min=0, max=1000)

                # Likes count should not exceed the number of users available
                likes_count = faker.random_int(min=0, max=min(10, len(users)))
                likes = faker.random_elements(users, unique=True, length=likes_count)

                # Create a new BlogPost object with a unique slug
                slug = None
                while not slug:
                    title = faker.sentence(nb_words=5)
                    potential_slug = slugify(title)
                    if not BlogPost.objects.filter(slug=potential_slug).exists():
                        slug = potential_slug

                post, created = BlogPost.objects.get_or_create(
                    category=category,
                    author=author,
                    status=status,
                    featured=featured,
                    view_count=view_count,
                    slug=slug,
                )
                post.tags.set(tags_list)
                post.likes.add(*likes)

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        title = faker.sentence(nb_words=5)
                        subtitle = faker.sentence(nb_words=10)
                        body = faker.paragraph(nb_sentences=10)
                        post.set_current_language(lang)
                        post.title = title
                        post.subtitle = subtitle
                        post.body = body
                        post.save()
                    created_posts.append(post)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_posts)} BlogPost instances."
            )
        )
