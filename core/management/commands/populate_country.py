# populate_country.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from country.models import Country
from helpers.seed import get_or_create_default_image

faker = Faker()


class Command(BaseCommand):
    help = "Seed country models including multilanguage data."

    @staticmethod
    def generate_alpha_2():
        while True:
            alpha_2 = faker.country_code(representation="alpha-2")
            if not Country.objects.filter(alpha_2=alpha_2).exists():
                return alpha_2

    @staticmethod
    def generate_alpha_3():
        while True:
            alpha_3 = faker.country_code(representation="alpha-3")
            if not Country.objects.filter(alpha_3=alpha_3).exists():
                return alpha_3

    @staticmethod
    def generate_iso_cc():
        while True:
            iso_cc = faker.random_number(3)
            if not Country.objects.filter(iso_cc=iso_cc).exists():
                return iso_cc

    @staticmethod
    def generate_phone_code():
        while True:
            phone_code = faker.random_number(3)
            if not Country.objects.filter(phone_code=phone_code).exists():
                return phone_code

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

        if total_countries < 1:
            self.stdout.write(
                self.style.WARNING("Total number of countries must be greater than 0.")
            )
            return

        img = get_or_create_default_image("uploads/country/no_photo.jpg")

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        if not available_languages:
            self.stdout.write(self.style.ERROR("No languages found."))
            return

        created_countries = []
        iterations = 0

        with transaction.atomic():
            for _ in range(total_countries):
                # Create a new Country object for this translation
                country, created = Country.objects.get_or_create(
                    alpha_2=self.generate_alpha_2(),
                    alpha_3=self.generate_alpha_3(),
                    iso_cc=self.generate_iso_cc(),
                    phone_code=self.generate_phone_code(),
                    image_flag=img,
                )

                if created:
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
                    created_countries.append(country)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(created_countries)} Country models."
            )
        )
