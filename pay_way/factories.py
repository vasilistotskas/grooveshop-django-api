import factory
from django.apps import apps
from django.conf import settings

from pay_way.enum.pay_way_enum import PayWayEnum
from pay_way.models import PayWay

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class PayWayTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Iterator([choice[0] for choice in PayWayEnum.choices])
    master = factory.SubFactory("pay_way.factories.PayWayFactory")

    class Meta:
        model = apps.get_model("pay_way", "PayWayTranslation")
        django_get_or_create = ("language_code", "master")


class PayWayFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean")
    cost = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)
    free_for_order_amount = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)
    icon = factory.django.ImageField(
        filename="pay_way_icon.jpg",
        color=factory.Faker("color"),
        width=256,
        height=256,
    )

    class Meta:
        model = PayWay
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            PayWayTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
