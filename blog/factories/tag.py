import factory
from django.apps import apps
from django.conf import settings

from blog.models.tag import BlogTag

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class BlogTagTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    master = factory.SubFactory("blog.factories.tag.BlogTagFactory")

    class Meta:
        model = apps.get_model("blog", "BlogTagTranslation")
        django_get_or_create = ("language_code", "master")


class BlogTagFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean")

    class Meta:
        model = BlogTag
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            BlogTagTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
