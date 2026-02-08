from decimal import Decimal

import factory
from django.apps import apps
from django.conf import settings

from devtools.factories import CustomDjangoModelFactory
from loyalty.models.tier import LoyaltyTier

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class LoyaltyTierTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker(
        "random_element",
        elements=["Bronze", "Silver", "Gold", "Platinum", "Diamond"],
    )
    description = factory.Faker("sentence")
    master = factory.SubFactory("loyalty.factories.tier.LoyaltyTierFactory")

    class Meta:
        model = apps.get_model("loyalty", "LoyaltyTierTranslation")
        django_get_or_create = ("language_code", "master")


class LoyaltyTierFactory(CustomDjangoModelFactory):
    auto_translations = False
    required_level = factory.Sequence(lambda n: n + 1)
    points_multiplier = Decimal("1.0")

    class Meta:
        model = LoyaltyTier
        django_get_or_create = ("required_level",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            LoyaltyTierTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
