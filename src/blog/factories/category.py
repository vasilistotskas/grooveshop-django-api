import uuid

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from blog.models.category import BlogCategory
from devtools.factories import CustomDjangoModelFactory

fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def generate_unique_blog_category_filename():
    return f"blog_category_{uuid.uuid4().hex[:8]}.jpg"


class BlogCategoryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker(
        "random_element",
        elements=[
            "Technology",
            "Lifestyle",
            "Travel",
            "Food & Recipes",
            "Health & Wellness",
            "Fashion",
            "Business",
            "Entertainment",
            "Sports",
            "Education",
            "Finance",
            "DIY & Crafts",
            "Parenting",
            "Fitness",
            "Beauty",
            "Home Decor",
            "Photography",
            "Marketing",
            "Science",
            "Politics",
            "Environment",
            "Gaming",
            "Music",
            "Art & Design",
            "Career Development",
            "Personal Finance",
            "Book Reviews",
            "Movie Reviews",
            "Product Reviews",
        ],
    )
    description = factory.Faker("text", max_nb_chars=200)
    master = factory.SubFactory("blog.factories.category.BlogCategoryFactory")

    class Meta:
        model = apps.get_model("blog", "BlogCategoryTranslation")
        django_get_or_create = ("language_code", "master")


class BlogCategoryFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    image = factory.django.ImageField(
        filename=factory.LazyFunction(generate_unique_blog_category_filename),
        width=1920,
        height=1080,
        color="green",
        format="JPEG",
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
            BlogCategoryTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
