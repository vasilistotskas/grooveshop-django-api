import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from blog.models.category import BlogCategory
from core.factories import CustomDjangoModelFactory

fake = Faker()

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class BlogCategoryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    description = factory.Faker("paragraph")
    master = factory.SubFactory("blog.factories.category.BlogCategoryFactory")

    class Meta:
        model = apps.get_model("blog", "BlogCategoryTranslation")
        django_get_or_create = ("language_code", "master")


class BlogCategoryFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    image = factory.django.ImageField(
        filename="blog_category.jpg",
        color=factory.Faker("color"),
        width=1920,
        height=1080,
    )
    parent = None

    class Meta:
        model = BlogCategory
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            BlogCategoryTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
