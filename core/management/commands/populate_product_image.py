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

        img = get_or_create_default_image("uploads/products/no_photo.jpg")

        created_images = []
        with transaction.atomic():
            for product in products:
                if not product.product_images.filter(is_main=True).exists():
                    main_image = ProductImage.objects.create(
                        title="Main Image",
                        product=product,
                        is_main=True,
                        image=img,
                    )

                    created_images.append(main_image)

                for _ in range(total_images - 1):
                    title = faker.word()
                    image = ProductImage.objects.create(
                        title=title,
                        product=product,
                        is_main=False,
                        image=img,
                    )
                    created_images.append(image)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_images)} ProductImage instances."
            )
        )
