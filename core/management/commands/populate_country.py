# populate_country.py
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from country.models import Country
from helpers.seed import get_or_create_default_image

faker = Faker()


class Command(BaseCommand):
    help = "Seed country models including multilanguage data."

    def add_arguments(self, parser):
        parser.add_argument(
            "total_countries",
            type=int,
            help="Indicates the number of countries to be seeded.",
            default=20,
            nargs="?",
        )
        parser.add_argument(
            "max_iterations",
            type=int,
            help="Indicates the maximum " "number of iterations to " "be used.",
            default=200,
            nargs="?",
        )

    def handle(self, *args, **options):
        total_countries = options["total_countries"]
        total_time = 0
        start_time = time.time()
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if total_countries < 1:
            self.stdout.write(
                self.style.WARNING("Total number of countries must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/country/no_photo.jpg")

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        iterations = 0
        objects_to_insert = []
        with transaction.atomic():
            for _ in range(total_countries):
                alpha_2 = faker.unique.country_code(representation="alpha-2")
                alpha_3 = faker.unique.country_code(representation="alpha-3")
                iso_cc = faker.unique.random_number(3)
                phone_code = faker.unique.random_number(3)

                alpha_2_exists = (
                    True if Country.objects.filter(alpha_2=alpha_2).exists() else False
                )
                alpha_3_exists = (
                    True if Country.objects.filter(alpha_3=alpha_3).exists() else False
                )
                iso_cc_exists = (
                    True if Country.objects.filter(iso_cc=iso_cc).exists() else False
                )
                phone_code_exists = (
                    True
                    if Country.objects.filter(phone_code=phone_code).exists()
                    else False
                )

                if (
                    alpha_2_exists
                    or alpha_3_exists
                    or iso_cc_exists
                    or phone_code_exists
                ):
                    continue

                country = Country(
                    alpha_2=alpha_2,
                    alpha_3=alpha_3,
                    iso_cc=iso_cc,
                    phone_code=phone_code,
                    image_flag=img,
                )
                objects_to_insert.append(country)
            Country.objects.bulk_create(objects_to_insert)

            for country in objects_to_insert:
                for lang in available_languages:
                    lang_seed = hash(f"{country.alpha_2}{lang}")
                    faker.seed_instance(lang_seed)
                    name = f"{faker.country()}-{lang}"
                    while (
                        Country.objects.filter(translations__name=name).exists()
                        and iterations < options["max_iterations"]
                    ):
                        name = f"{faker.country()}-{lang}"
                        iterations += 1
                    country.set_current_language(lang)
                    country.name = name
                    country.save()

        end_time = time.time()
        execution_time = end_time - start_time
        total_time += execution_time
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(objects_to_insert)} Country instances created successfully in {execution_time:.2f} seconds."
            )
        )
