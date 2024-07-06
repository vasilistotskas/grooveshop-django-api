import factory
from django.apps import apps
from django.conf import settings

from blog.models.author import BlogAuthor

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class BlogAuthorTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    bio = factory.Faker("paragraph")
    master = factory.SubFactory("blog.factories.author.BlogAuthorFactory")

    class Meta:
        model = apps.get_model("blog", "BlogAuthorTranslation")
        django_get_or_create = ("language_code", "master")


class BlogAuthorFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    website = factory.Faker("url")

    class Meta:
        model = BlogAuthor
        django_get_or_create = ("user",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            BlogAuthorTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
