# populate_region.py
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
            default=20,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_regions = options["total_regions"]

        if total_regions < 1:
            self.stdout.write(
                self.style.WARNING("Total number of regions must be greater than 0.")
            )
            return

        # Get all existing countries
        countries = list(Country.objects.all())

        if not countries:
            self.stdout.write(
                self.style.ERROR("Insufficient data. Aborting seeding Region model.")
            )
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        created_regions = []
        with transaction.atomic():
            for _ in range(total_regions):
                country = faker.random_element(countries)
                alpha = None

                # Ensure the alpha value is unique for each region
                while not alpha or Region.objects.filter(alpha=alpha).exists():
                    alpha = faker.uuid4().split("-")[-1].upper()[:10]

                # Create a new Region object
                region, created = Region.objects.get_or_create(
                    alpha=alpha,
                    alpha_2=country,
                )

                if created:
                    for lang in available_languages:
                        faker.seed_instance(lang)
                        name = faker.word()
                        region.set_current_language(lang)
                        region.name = name
                        region.save()
                    created_regions.append(region)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_regions)} Region instances."
            )
        )
