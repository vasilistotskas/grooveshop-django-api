import os
from random import randrange

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import BaseCommand
from django.utils.text import slugify
from faker import Faker

from app.settings import BASE_DIR
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from blog.models.tag import BlogTag

User = get_user_model()


class Command(BaseCommand):
    @staticmethod
    def create_posts(author_id, img, category_id):
        faker = Faker()
        for _ in range(5):
            name = faker.name()
            BlogPost.objects.create(
                title=name,
                subtitle=faker.text(20),
                slug=slugify(name),
                body=faker.text(100),
                meta_description=faker.text(10),
                is_published=True,
                author=BlogAuthor.objects.get(id=author_id),
                image=img,
                category_id=category_id,
            )

    def handle(self, *args, **options):
        faker = Faker()

        user_id = randrange(1, 10)
        website = faker.text(20)
        bio = faker.text(50)

        img = "uploads/products/no_photo.jpg"
        if not default_storage.exists(img):
            img_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
            img = SimpleUploadedFile(
                name="no_photo.jpg",
                content=open(img_path, "rb").read(),
                content_type="image/jpeg",
            )

        author, created = BlogAuthor.objects.get_or_create(
            defaults={"user_id": user_id, "website": website, "bio": bio}
        )

        try:
            intermediate_category = BlogCategory.objects.get(
                slug="intermediate",
            )
            self.create_posts(author.id, img, intermediate_category.id)
        except BlogCategory.DoesNotExist:
            intermediate_category = BlogCategory(
                name="Intermediate",
                slug="intermediate",
                description=faker.text(100),
            )
            intermediate_category.save()
            self.create_posts(author.id, img, intermediate_category.id)

        try:
            beginner_category = BlogCategory.objects.get(
                slug="beginner",
            )
            self.create_posts(author.id, img, beginner_category.id)
        except BlogCategory.DoesNotExist:
            beginner_category = BlogCategory(
                name="Beginner",
                slug="beginner",
                description=faker.text(100),
            )
            beginner_category.save()
            self.create_posts(author.id, img, beginner_category.id)

        try:
            master_category = BlogCategory.objects.get(
                slug="master",
            )
            self.create_posts(author.id, img, master_category.id)
        except BlogCategory.DoesNotExist:
            master_category = BlogCategory(
                name="Master",
                slug="master",
                description=faker.text(100),
            )
            master_category.save()
            self.create_posts(author.id, img, master_category.id)

        for _ in range(2):
            BlogTag.objects.create(
                name=faker.name(),
            )

        self.stdout.write(self.style.SUCCESS("Success"))
