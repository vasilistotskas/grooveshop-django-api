# populate_vat.py
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

        if total_vats < 1:
            self.stdout.write(
                self.style.WARNING("Total number of Vats must be greater than 0.")
            )
            return

        created_vats = []
        with transaction.atomic():
            for _ in range(total_vats):
                value = Decimal(faker.random.uniform(5.0, 25.0))

                # Create a new Vat object
                vat = Vat.objects.create(value=value)

                created_vats.append(vat)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_vats)} Vat instances."
            )
        )
