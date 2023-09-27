# populate_pay_way.py
import time
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from helpers.seed import get_or_create_default_image
from pay_way.enum.pay_way_enum import PayWayEnum
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
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_pay_ways < 1:
            self.stdout.write(
                self.style.WARNING("Total number of PayWays must be greater than 0.")
            )
            return

        pay_way_choices = [choice[0] for choice in PayWayEnum.choices]

        img = get_or_create_default_image("uploads/pay_way/no_photo.jpg")

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        picked_names = []
        with transaction.atomic():
            for _ in range(total_pay_ways):
                active = faker.boolean(chance_of_getting_true=90)
                cost = Decimal(faker.random.uniform(0.0, 10.0))
                free_for_order_amount = Decimal(faker.random.uniform(0.0, 100.0))

                name = faker.random_element(pay_way_choices)
                pay_way_name_exists = PayWay.objects.filter(
                    translations__name=name
                ).exists()

                if pay_way_name_exists or name in picked_names:
                    continue

                pay_way = PayWay(
                    active=active,
                    cost=cost,
                    free_for_order_amount=free_for_order_amount,
                    icon=img,
                )
                objects_to_insert.append(pay_way)
                picked_names.append(name)
            PayWay.objects.bulk_create(objects_to_insert)

            for pay_way in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{pay_way.id}{lang}")
                    faker.seed_instance(lang_seed)
                    pay_way.set_current_language(lang)
                    pay_way.name = name
                    pay_way.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} PayWay instances created successfully in {execution_time:.2f} seconds."
            )
        )
