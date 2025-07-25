import factory
from django.apps import apps
from django.conf import settings

from blog.models.tag import BlogTag

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class BlogTagTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("text", max_nb_chars=45)
    master = factory.SubFactory("blog.factories.tag.BlogTagFactory")

    class Meta:
        model = apps.get_model("blog", "BlogTagTranslation")
        django_get_or_create = ("language_code", "master")


class BlogTagFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean", chance_of_getting_true=90)

    class Meta:
        model = BlogTag
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted is not None:
            self.translations.all().delete()

        if extracted is None and not self.translations.exists():
            translations = [
                BlogTagTranslationFactory(language_code=lang, master=self)
                for lang in available_languages
            ]
        else:
            translations = extracted or []

        for translation in translations:
            translation.master = self
            translation.save()
