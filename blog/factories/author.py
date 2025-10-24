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
    bio = factory.Faker(
        "random_element",
        elements=[
            "Passionate writer and storyteller with a love for exploring new ideas and perspectives.",
            "Tech enthusiast and software developer sharing insights on the latest innovations.",
            "Travel blogger documenting adventures around the world and cultural experiences.",
            "Food lover and chef sharing delicious recipes and culinary tips.",
            "Health and wellness coach helping people live their best lives.",
            "Fashion expert and style consultant with years of industry experience.",
            "Business strategist and entrepreneur sharing lessons from the startup world.",
            "Entertainment journalist covering the latest news in film, TV, and music.",
            "Sports analyst and former athlete with in-depth knowledge of the game.",
            "Educator and academic researcher passionate about learning and development.",
            "Finance professional providing practical advice on money management and investing.",
            "Creative DIY enthusiast sharing craft ideas and home improvement projects.",
            "Parenting blogger offering tips and support for modern families.",
            "Fitness trainer dedicated to helping others achieve their health goals.",
            "Beauty expert and makeup artist sharing the latest trends and tutorials.",
            "Interior designer with a passion for creating beautiful, functional spaces.",
            "Professional photographer capturing moments and teaching the art of photography.",
            "Marketing specialist helping brands connect with their audiences.",
            "Science communicator making complex topics accessible and engaging.",
            "Environmental activist working towards a more sustainable future.",
        ],
    )
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
