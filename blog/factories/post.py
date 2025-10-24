import importlib
import uuid

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from blog.factories.comment import BlogCommentFactory
from blog.factories.tag import BlogTagFactory
from blog.models.post import BlogPost
from core.factories import CustomDjangoModelFactory


fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def generate_unique_blog_filename():
    return f"blog_post_{uuid.uuid4().hex[:8]}.jpg"


def get_or_create_category():
    if apps.get_model("blog", "BlogCategory").objects.exists():
        return (
            apps.get_model("blog", "BlogCategory").objects.order_by("?").first()
        )
    else:
        category_factory_module = importlib.import_module(
            "blog.factories.category"
        )
        category_factory_class = category_factory_module.BlogCategoryFactory
        return category_factory_class.create()


def get_or_create_author():
    if apps.get_model("blog", "BlogAuthor").objects.exists():
        return (
            apps.get_model("blog", "BlogAuthor").objects.order_by("?").first()
        )
    else:
        author_factory_module = importlib.import_module("blog.factories.author")
        author_factory_class = author_factory_module.BlogAuthorFactory
        return author_factory_class.create()


class BlogPostTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker(
        "random_element",
        elements=[
            "10 Tips for Improving Your Productivity",
            "The Ultimate Guide to Healthy Eating",
            "How to Start Your Own Business in 2025",
            "Best Travel Destinations for Summer",
            "Understanding Digital Marketing Strategies",
            "The Future of Artificial Intelligence",
            "Simple Ways to Save Money Every Month",
            "Top 10 Fitness Exercises for Beginners",
            "Home Organization Hacks That Actually Work",
            "The Art of Effective Communication",
            "Building a Successful Career in Tech",
            "Sustainable Living: Easy Steps to Get Started",
            "Mastering Time Management Skills",
            "The Benefits of Meditation and Mindfulness",
            "How to Create Engaging Content",
            "Fashion Trends to Watch This Season",
            "Essential Photography Tips for Beginners",
            "The Complete Guide to Remote Work",
            "Healthy Breakfast Ideas for Busy Mornings",
            "Understanding Cryptocurrency and Blockchain",
            "DIY Home Improvement Projects",
            "The Psychology of Success",
            "Best Practices for Social Media Marketing",
            "Cooking Techniques Every Chef Should Know",
            "How to Build Strong Relationships",
            "The Science of Sleep and Rest",
            "Financial Planning for Your Future",
            "Creative Writing Tips and Techniques",
            "The Impact of Climate Change",
            "Modern Interior Design Ideas",
        ],
    )
    subtitle = factory.Faker("text", max_nb_chars=150)
    body = factory.Faker("text", max_nb_chars=2000)
    master = factory.SubFactory("blog.factories.post.BlogPostFactory")

    class Meta:
        model = apps.get_model("blog", "BlogPostTranslation")
        django_get_or_create = ("language_code", "master")


class BlogPostFactory(CustomDjangoModelFactory):
    auto_translations = False

    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    image = factory.django.ImageField(
        filename=factory.LazyFunction(generate_unique_blog_filename),
        width=1200,
        height=630,
        color="purple",
    )
    category = factory.LazyFunction(get_or_create_category)
    author = factory.LazyFunction(get_or_create_author)
    is_published = factory.Faker("pybool", truth_probability=80)
    featured = factory.Faker("pybool", truth_probability=20)
    view_count = factory.Faker("random_int", min=0, max=10000)

    class Meta:
        model = BlogPost
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def num_tags(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            tags = BlogTagFactory.create_batch(extracted)
            self.tags.add(*tags)

    @factory.post_generation
    def num_comments(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            BlogCommentFactory.create_batch(extracted, post=self)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            BlogPostTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
