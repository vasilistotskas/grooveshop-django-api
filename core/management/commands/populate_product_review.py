# populate_product_review.py
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.enum.review import RateEnum
from product.enum.review import ReviewStatusEnum
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
            default=1000,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_reviews = options["total_reviews"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_reviews < 1:
            self.stdout.write(
                self.style.WARNING("Total number of reviews must be greater than 0.")
            )
            return

        users = list(User.objects.all())
        products = list(Product.objects.all())

        if not users or not products:
            self.stdout.write(
                self.style.ERROR(
                    "Insufficient data. Aborting seeding ProductReview model."
                )
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        rate_choices = [choice[0] for choice in RateEnum.choices]
        status_choices = [choice[0] for choice in ReviewStatusEnum.choices]

        objects_to_insert = []
        user_product_review = []
        with transaction.atomic():
            for _ in range(total_reviews):
                user = faker.random_element(users)
                product = faker.random_element(products)
                rate = faker.random_element(rate_choices)
                status = faker.random_element(status_choices)

                user_product_pair = (user, product)
                existing_review = ProductReview.objects.filter(
                    user=user, product=product
                ).exists()

                if not existing_review and user_product_pair not in user_product_review:
                    user_product_review.append(user_product_pair)
                    review = ProductReview(
                        product=product,
                        user=user,
                        rate=rate,
                        status=status,
                    )
                    objects_to_insert.append(review)
            ProductReview.objects.bulk_create(objects_to_insert)

            for review in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{review.id}{lang}")
                    faker.seed_instance(lang_seed)
                    comment = faker.text(max_nb_chars=250)
                    review.set_current_language(lang)
                    review.comment = comment
                    review.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} ProductReview instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
