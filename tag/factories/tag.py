import factory
from django.apps import apps
from django.conf import settings

from tag.models.tag import Tag


available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class TagTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    label = factory.Faker("word")
    master = factory.SubFactory("tag.factories.tag.TagFactory")

    class Meta:
        model = apps.get_model("tag", "TagTranslation")
        django_get_or_create = ("language_code", "master")


class TagFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean")

    class Meta:
        model = Tag
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            TagTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
