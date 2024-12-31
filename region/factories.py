import importlib

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import CustomDjangoModelFactory
from region.models import Region

fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


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
    name = factory.Faker("city")
    master = factory.SubFactory("region.factories.RegionFactory")

    class Meta:
        model = apps.get_model("region", "RegionTranslation")
        django_get_or_create = ("language_code", "master")


class RegionFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("alpha", lambda: fake.bothify(text="???-######")),
    ]

    country = factory.LazyFunction(get_or_create_country)

    class Meta:
        model = Region
        django_get_or_create = ("alpha",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            RegionTranslationFactory(language_code=lang, master=self)
            for lang in [
                lang["code"]
                for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
            ]
        ]

        for translation in translations:
            translation.save()
