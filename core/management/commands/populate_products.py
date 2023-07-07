import os
from random import randrange

from app.settings import BASE_DIR
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.models.product import ProductImages
from vat.models import Vat
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import BaseCommand
from django.utils.text import slugify
from django.utils.timezone import now
from faker import Faker


class Command(BaseCommand):
    def handle(self, *args, **options):
        faker = Faker()
        user_id = randrange(1, 10)
        i = 1

        img = "uploads/products/no_photo.jpg"
        if not default_storage.exists(img):
            img_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
            img = SimpleUploadedFile(
                name="no_photo.jpg",
                content=open(img_path, "rb").read(),
                content_type="image/jpeg",
            )

        for _ in range(3):
            vat = randrange(0, 100)
            Vat.objects.create(value=vat)

        for _ in range(2):
            name = faker.name()
            category = ProductCategory.objects.create(
                name=name,
                slug=slugify(name),
                description=faker.text(10),
            )

            for _ in range(200):
                product_price = randrange(20, 300)
                name = faker.text(20) + str(i)
                try:
                    product = Product.objects.get(
                        name=name,
                    )
                except Product.DoesNotExist:
                    product = Product.objects.create(
                        category_id=category.id,
                        name=name,
                        slug=slugify(name),
                        description=faker.text(50),
                        price=product_price,
                        active="True",
                        stock=100,
                        created_at=now(),
                        vat_id=1,
                    )
                    i = i + 1

                    ProductImages.objects.create(
                        title=faker.text(5),
                        product_id=product.id,
                        image=img,
                        is_main=True,
                    )

                    for _ in range(2):
                        try:
                            ProductFavourite.objects.get(
                                user_id=user_id,
                                product_id=product.id,
                            )
                        except ProductFavourite.DoesNotExist:
                            ProductFavourite.objects.create(
                                user_id=user_id,
                                product_id=product.id,
                            )

        self.stdout.write(self.style.SUCCESS("Success"))
