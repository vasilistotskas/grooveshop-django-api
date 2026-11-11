from decimal import Decimal

import factory
from django.apps import apps
from django.conf import settings

from devtools.factories import CustomDjangoModelFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.models import Promotion, PromotionCode

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class PromotionTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Sequence(lambda n: f"Promotion {n}")
    description = factory.Faker("sentence")
    master = factory.SubFactory(
        "promotion.factories.promotion.PromotionFactory"
    )

    class Meta:
        model = apps.get_model("promotion", "PromotionTranslation")
        django_get_or_create = ("language_code", "master")


class PromotionFactory(CustomDjangoModelFactory):
    auto_translations = False
    trigger = PromotionTrigger.CODE
    benefit_type = BenefitType.PERCENTAGE
    benefit_value = Decimal("10.0")
    target_scope = TargetScope.ORDER
    is_active = True
    stackable = False
    priority = 0

    class Meta:
        model = Promotion
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            PromotionTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()


class PromotionCodeFactory(factory.django.DjangoModelFactory):
    promotion = factory.SubFactory(PromotionFactory)
    code = factory.Sequence(lambda n: f"CODE{n:06d}")
    is_active = True

    class Meta:
        model = PromotionCode
        django_get_or_create = ("code",)
