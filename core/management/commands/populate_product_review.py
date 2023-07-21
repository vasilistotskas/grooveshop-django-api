# populate_product_review.py
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.enum.review import RateEnum
from product.enum.review import StatusEnum
from product.models.product import Product
from product.models.review import ProductReview

faker = Faker()
User = get_user_model()


class Command(BaseCommand):
    help = "Seed ProductReview model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_reviews",
            type=int,
            help="Indicates the number of reviews to be seeded.",
            default=500,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_reviews = options["total_reviews"]

        if total_reviews < 1:
            self.stdout.write(
                self.style.WARNING("Total number of reviews must be greater than 0.")
            )
            return

        # Get all existing users and products
        users = list(User.objects.all())
        products = list(Product.objects.all())

        if not users or not products:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding ProductReview model."
                )
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        rate_choices = [choice[0] for choice in RateEnum.choices()]
        status_choices = [choice[0] for choice in StatusEnum.choices()]

        created_reviews = []
        with transaction.atomic():
            for _ in range(total_reviews):
                user = faker.random_element(users)
                product = faker.random_element(products)
                rate = faker.random_element(rate_choices)
                status = faker.random_element(status_choices)

                # Create a new ProductReview object
                review, created = ProductReview.objects.get_or_create(
                    product=product,
                    user=user,
                    rate=rate,
                    status=status,
                )

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        comment = faker.text(max_nb_chars=250)
                        review.set_current_language(lang)
                        review.comment = comment
                        review.save()
                    created_reviews.append(review)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_reviews)} ProductReview instances."
            )
        )
