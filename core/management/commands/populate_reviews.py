from random import randrange

from product.models.review import ProductReview
from django.core.management import BaseCommand
from django.utils.timezone import now
from faker import Faker


class Command(BaseCommand):
    def handle(self, *args, **options):
        faker = Faker()

        for _ in range(2000):
            rate = randrange(0, 10)
            user_id = randrange(1, 10)
            product_id = randrange(2, 400)
            try:
                ProductReview.objects.get(
                    user_id=user_id,
                    product_id=product_id,
                )
            except ProductReview.DoesNotExist:
                ProductReview.objects.create(
                    user_id=user_id,
                    product_id=product_id,
                    comment=faker.text(50),
                    rate=rate,
                    status="True",
                    created_at=now(),
                    updated_at=now(),
                )
        self.stdout.write(self.style.SUCCESS("Success"))
