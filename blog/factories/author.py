import importlib

import factory
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model

from blog.models.author import BlogAuthor

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


class BlogAuthorTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    bio = factory.Faker("paragraph")
    master = factory.SubFactory("blog.factories.author.BlogAuthorFactory")

    class Meta:
        model = apps.get_model("blog", "BlogAuthorTranslation")
        django_get_or_create = ("language_code", "master")


class BlogAuthorFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
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
            BlogAuthorTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
