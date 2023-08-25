import os
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from helpers.seed import get_or_create_default_image
from product.models.image import ProductImage
from product.models.product import Product

faker = Faker()


class Command(BaseCommand):
    help = "Seed ProductImage model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_images",
            type=int,
            help="Indicates the number of images to be seeded per product.",
            default=5,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_images = options["total_images"]

        if total_images < 1:
            self.stdout.write(
                self.style.WARNING("Total number of images must be greater than 0.")
            )
            return

        # Get all existing products
        products = list(Product.objects.all())

        if not products:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding ProductImage model."
                )
            )
            return

        img_folder = os.path.join(os.path.dirname(__file__), "images", "products")
        img_files = os.listdir(img_folder)
        if not img_files:
            self.stdout.write(
                self.style.ERROR("No image files found in the seed_images folder.")
            )
            return

        created_images = []
        with transaction.atomic():
            for product in products:
                if not product.product_images.filter(is_main=True).exists():
                    img_filename = random.choice(img_files)
                    img_path = os.path.join(img_folder, img_filename)
                    main_image = ProductImage.objects.create(
                        title="Main Image",
                        product=product,
                        is_main=True,
                        image=get_or_create_default_image(
                            img_path, use_default_storage=False
                        ),
                    )
                    created_images.append(main_image)

                for _ in range(total_images - 1):
                    title = faker.word()
                    img_filename = random.choice(img_files)
                    img_path = os.path.join(img_folder, img_filename)
                    image = ProductImage.objects.create(
                        title=title,
                        product=product,
                        is_main=False,
                        image=get_or_create_default_image(
                            img_path, use_default_storage=False
                        ),
                    )
                    created_images.append(image)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_images)} ProductImage instances."
            )
        )
