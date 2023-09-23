# populate_region.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from country.models import Country
from region.models import Region

faker = Faker()


class Command(BaseCommand):
    help = "Seed Region model."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_regions",
            type=int,
            help="Indicates the number of regions to be seeded.",
            default=100,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_regions = options["total_regions"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_regions < 1:
            self.stdout.write(
                self.style.WARNING("Total number of regions must be greater than 0.")
            )
            return

        countries = list(Country.objects.all())

        if not countries:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Region model.")
            )
            return

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_regions):
                country = faker.random_element(countries)
                alpha = faker.unique.uuid4().split("-")[-1].upper()[:10]

                region = Region(
                    alpha=alpha,
                    alpha_2=country,
                )
                objects_to_insert.append(region)
            Region.objects.bulk_create(objects_to_insert)

            for region in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{alpha}{lang}")
                    faker.seed_instance(lang_seed)
                    name = faker.word()
                    region.set_current_language(lang)
                    region.name = name
                    region.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} Region instances created successfully "
                f"in {execution_time:.2f} seconds."
            )
        )
