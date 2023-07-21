from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from helpers.seed import get_or_create_default_image
from product.models.images import ProductImages
from product.models.product import Product

faker = Faker()


class Command(BaseCommand):
    help = "Seed ProductImages model."

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
                    "Insufficient data. Aborting seeding ProductImages model."
                )
            )
            return

        img = get_or_create_default_image("uploads/products/no_photo.jpg")

        created_images = []
        with transaction.atomic():
            for product in products:
                main_image = ProductImages.objects.create(
                    title="Main Image",
                    product=product,
                    is_main=True,
                    image=img,
                )

                created_images.append(main_image)

                for _ in range(total_images - 1):
                    title = faker.word()
                    image = ProductImages.objects.create(
                        title=title,
                        product=product,
                        is_main=False,
                        image=img,
                    )
                    created_images.append(image)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_images)} ProductImages instances."
            )
        )
