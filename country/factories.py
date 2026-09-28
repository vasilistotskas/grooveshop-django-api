import random
import string

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from country.models import Country
from devtools.factories import (
    CustomDjangoModelFactory,
    SeedingResult,
    custom_seeding,
)

fake = Faker()

# ISO 3166-1's own "user-assigned" alpha-2/alpha-3 ranges: reserved
# indefinitely, never allocated to a real country by the standard, so
# a value drawn from here can never collide with
# ``country/migrations/0012_seed_iso_countries.py``'s full ISO seed —
# real Faker country codes exist in `unique_model_fields`' DB-conflict
# retry loop, but only ~195 of them, all now pre-seeded, so every
# attempt failed and exhausted it. Same reserved-range trick
# ``tests/integration/region/test_filters.py`` already uses for the
# identical reason.
_RESERVED_ALPHA_2 = (
    ["AA", "ZZ"]
    + [f"Q{c}" for c in "MNOPQRSTUVWXYZ"]
    + [f"X{c}" for c in string.ascii_uppercase]
)


def _random_reserved_alpha_2() -> str:
    return random.choice(_RESERVED_ALPHA_2)


def _random_reserved_alpha_3() -> str:
    return "X" + "".join(random.choices(string.ascii_uppercase, k=2))


available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

LANGUAGE_COUNTRY_MAPPING = getattr(settings, "LANGUAGE_COUNTRY_MAPPING", {})


class CountryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.LazyAttribute(
        lambda obj: f"Country Name ({obj.language_code})"
    )
    master = factory.SubFactory("country.factories.CountryFactory")

    class Meta:
        model = apps.get_model("country", "CountryTranslation")
        django_get_or_create = ("language_code", "master")


@custom_seeding(
    description="Creates countries based on PARLER_LANGUAGES settings (el→GR, en→GB, de→DE)",
    settings_key="PARLER_LANGUAGES",
)
class CountryFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("alpha_2", _random_reserved_alpha_2),
        ("alpha_3", _random_reserved_alpha_3),
        ("phone_code", lambda: fake.random_int(min=1, max=9999)),
    ]

    image_flag = factory.django.ImageField(
        filename=factory.LazyAttribute(lambda _: f"{fake.word()}.png"),
        color=factory.Faker("color"),
        width=640,
        height=480,
    )

    class Meta:
        model = Country
        django_get_or_create = ("alpha_2",)
        skip_postgeneration_save = True

    @classmethod
    def custom_seed(cls, **kwargs) -> SeedingResult:
        result = SeedingResult(created_count=0)

        verbose = kwargs.get("verbose", True)

        for lang_code in available_languages:
            if lang_code in LANGUAGE_COUNTRY_MAPPING:
                country_data = LANGUAGE_COUNTRY_MAPPING[lang_code]

                existing_country = Country.objects.filter(
                    alpha_2=country_data["alpha_2"]
                ).first()

                if existing_country:
                    if verbose:
                        print(
                            f"Country {country_data['alpha_2']} already exists, skipping..."
                        )
                    result.skipped_count += 1
                    continue

                try:
                    country = Country.objects.create(
                        alpha_2=country_data["alpha_2"],
                        alpha_3=country_data["alpha_3"],
                        iso_cc=country_data.get("iso_cc"),
                        phone_code=country_data["phone_code"],
                    )

                    for trans_lang in available_languages:
                        country_name = country_data["names"].get(
                            trans_lang, country_data["names"]["en"]
                        )
                        translation_model = apps.get_model(
                            "country", "CountryTranslation"
                        )
                        translation_model.objects.get_or_create(
                            language_code=trans_lang,
                            master=country,
                            defaults={"name": country_name},
                        )

                    result.created_count += 1
                    if verbose:
                        print(f"Created country: {country_data['alpha_2']}")

                except Exception as e:
                    error_msg = f"Failed to create country {country_data['alpha_2']}: {e!s}"
                    result.errors.append(error_msg)
                    if verbose:
                        print(f"Error: {error_msg}")

        return result

    @factory.post_generation
    def num_regions(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            from region.factories import RegionFactory

            RegionFactory.create_batch(extracted, country=self)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        if not hasattr(self, "_translations_created"):
            translations = extracted or [
                CountryTranslationFactory(language_code=lang, master=self)
                for lang in available_languages
            ]

            for translation in translations:
                translation.save()
