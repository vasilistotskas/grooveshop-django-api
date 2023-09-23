# populate_vat.py
import time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from vat.models import Vat

faker = Faker()


class Command(BaseCommand):
    help = "Seed Vat model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_vats",
            type=int,
            help="Indicates the number of Vats to be seeded.",
            default=5,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_vats = options["total_vats"]
        total_time = 0
        start_time = time.time()

        if total_vats < 1:
            self.stdout.write(
                self.style.WARNING("Total number of Vats must be greater than 0.")
            )
            return

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_vats):
                value = Decimal(faker.random.uniform(5.0, 25.0))

                vat = Vat(value=value)
                objects_to_insert.append(vat)
            Vat.objects.bulk_create(objects_to_insert)

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} Vat instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
