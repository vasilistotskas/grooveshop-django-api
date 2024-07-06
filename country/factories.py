import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import CustomDjangoModelFactory
from country.models import Country

fake = Faker()

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class CountryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("country")
    master = factory.SubFactory("country.factories.CountryFactory")

    class Meta:
        model = apps.get_model("country", "CountryTranslation")
        django_get_or_create = ("language_code", "master")


class CountryFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("alpha_2", lambda: fake.country_code(representation="alpha-2")),
        ("alpha_3", lambda: fake.country_code(representation="alpha-3")),
        ("iso_cc", lambda: fake.random_int(min=1, max=999)),
        ("phone_code", lambda: fake.random_int(min=1, max=999)),
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

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            CountryTranslationFactory(language_code=lang, master=self)
            for lang in [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
        ]

        for translation in translations:
            translation.save()
