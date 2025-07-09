import importlib

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import (
    CustomDjangoModelFactory,
    custom_seeding,
    SeedingResult,
)
from region.models import Region

fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

COUNTRY_REGIONS_MAPPING = getattr(settings, "COUNTRY_REGIONS_MAPPING", {})


def get_or_create_country():
    if apps.get_model("country", "Country").objects.exists():
        return (
            apps.get_model("country", "Country").objects.order_by("?").first()
        )
    else:
        country_factory_module = importlib.import_module("country.factories")
        country_factory_class = country_factory_module.CountryFactory
        return country_factory_class.create()


class RegionTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.LazyAttribute(
        lambda obj: f"Region Name ({obj.language_code})"
    )
    master = factory.SubFactory("region.factories.RegionFactory")

    class Meta:
        model = apps.get_model("region", "RegionTranslation")
        django_get_or_create = ("language_code", "master")


@custom_seeding(
    description="Creates regions based on COUNTRY_REGIONS_MAPPING for existing countries",
    settings_key="COUNTRY_REGIONS_MAPPING",
)
class RegionFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("alpha", lambda: fake.bothify(text="???-######")),
    ]

    country = factory.LazyFunction(get_or_create_country)

    class Meta:
        model = Region
        django_get_or_create = ("alpha",)
        skip_postgeneration_save = True

    @classmethod
    def custom_seed(cls, **kwargs) -> SeedingResult:
        result = SeedingResult(created_count=0)
        verbose = kwargs.get("verbose", True)

        from country.models import Country

        for country_code, regions_data in COUNTRY_REGIONS_MAPPING.items():
            try:
                country = Country.objects.get(alpha_2=country_code)
            except Country.DoesNotExist:
                if verbose:
                    print(
                        f"Country {country_code} not found, skipping regions..."
                    )
                result.skipped_count += 1
                continue

            for region_data in regions_data:
                existing_region = Region.objects.filter(
                    alpha=region_data["alpha"]
                ).first()

                if existing_region:
                    if verbose:
                        print(
                            f"Region {region_data['alpha']} already exists, skipping..."
                        )
                    result.skipped_count += 1
                    continue

                try:
                    region = Region.objects.create(
                        alpha=region_data["alpha"],
                        country=country,
                    )

                    for trans_lang in available_languages:
                        region_name = region_data["names"].get(
                            trans_lang,
                            region_data["names"].get(
                                "en", region_data["alpha"]
                            ),
                        )
                        translation_model = apps.get_model(
                            "region", "RegionTranslation"
                        )
                        translation_model.objects.get_or_create(
                            language_code=trans_lang,
                            master=region,
                            defaults={"name": region_name},
                        )

                    result.created_count += 1
                    if verbose:
                        print(
                            f"Created region: {region_data['alpha']} in {country_code}"
                        )

                except Exception as e:
                    error_msg = f"Failed to create region {region_data['alpha']}: {str(e)}"
                    result.errors.append(error_msg)
                    if verbose:
                        print(f"Error: {error_msg}")

        return result

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        if not hasattr(self, "_translations_created"):
            translations = extracted or [
                RegionTranslationFactory(language_code=lang, master=self)
                for lang in available_languages
            ]

            for translation in translations:
                translation.save()
