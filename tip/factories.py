import factory
from django.apps import apps
from django.conf import settings

from tip.enum.tip_enum import TipKindEnum
from tip.models import Tip

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class TipTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("sentence", nb_words=4)
    content = factory.Faker("paragraph")
    url = factory.Faker("url")
    master = factory.SubFactory("tip.factories.TipFactory")

    class Meta:
        model = apps.get_model("tip", "TipTranslation")
        django_get_or_create = ("language_code", "master")


class TipFactory(factory.django.DjangoModelFactory):
    kind = factory.Iterator([choice[0] for choice in TipKindEnum.choices])
    icon = factory.django.ImageField(
        filename="tip_icon.jpg",
        color=factory.Faker("color"),
        width=256,
        height=256,
    )
    active = factory.Faker("boolean")

    class Meta:
        model = Tip
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            TipTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
