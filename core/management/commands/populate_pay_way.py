# populate_pay_way.py
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from helpers.seed import get_or_create_default_image
from order.enum.pay_way_enum import PayWayEnum
from pay_way.models import PayWay

faker = Faker()


class Command(BaseCommand):
    help = "Seed PayWay model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_pay_ways",
            type=int,
            help="Indicates the number of PayWays to be seeded.",
            default=5,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_pay_ways = options["total_pay_ways"]

        if total_pay_ways < 1:
            self.stdout.write(
                self.style.WARNING("Total number of PayWays must be greater than 0.")
            )
            return

        pay_way_choices = [choice[0] for choice in PayWayEnum.choices()]

        img = get_or_create_default_image("uploads/pay_way/no_photo.jpg")

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        created_pay_ways = []
        with transaction.atomic():
            for _ in range(total_pay_ways):
                active = faker.boolean(chance_of_getting_true=90)
                cost = Decimal(faker.random.uniform(0.0, 10.0))
                free_for_order_amount = Decimal(faker.random.uniform(0.0, 100.0))
                name = faker.random_element(pay_way_choices)

                # Create a new PayWay object
                pay_way, created = PayWay.objects.get_or_create(
                    active=active,
                    cost=cost,
                    free_for_order_amount=free_for_order_amount,
                    icon=img,
                )

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        pay_way.set_current_language(lang)
                        pay_way.name = name
                        pay_way.save()

                created_pay_ways.append(pay_way)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_pay_ways)} PayWay instances."
            )
        )
