import importlib

import factory
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model

from blog import signals
from blog.models.comment import BlogComment

User = get_user_model()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


def get_or_create_post():
    if apps.get_model("blog", "BlogPost").objects.exists():
        return apps.get_model("blog", "BlogPost").objects.order_by("?").first()
    else:
        comment_factory_module = importlib.import_module("blog.factories.post")
        comment_factory_class = comment_factory_module.BlogPostFactory
        return comment_factory_class.create()


class BlogCommentTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    content = factory.Faker(
        "random_element",
        elements=[
            "Great article! Very informative and well-written.",
            "Thanks for sharing this insightful post!",
            "I completely agree with your points here.",
            "This is exactly what I was looking for. Thank you!",
            "Interesting perspective. I hadn't thought about it that way.",
            "Could you elaborate more on this topic?",
            "Excellent work! Looking forward to more content like this.",
            "Very helpful tips. I'll definitely try these out.",
            "This post really resonated with me. Thank you!",
            "I have a different opinion on this matter...",
            "Well researched and thoughtfully presented.",
            "This is super useful! Bookmarking for later.",
            "Love your writing style! Keep it up!",
            "Can't wait to see what you write about next.",
            "This changed my perspective completely.",
            "I learned so much from reading this. Thanks!",
            "Great insights! Very practical advice.",
            "I'm sharing this with all my friends!",
            "This article came at the perfect time for me.",
            "Your content always delivers value. Thank you!",
            "I disagree with some points, but overall well done.",
            "More people need to read this. Amazing post!",
            "This is exactly the kind of content I enjoy.",
            "Thought-provoking and well-articulated.",
            "Thank you for addressing this important topic.",
        ],
    )
    master = factory.SubFactory("blog.factories.comment.BlogCommentFactory")

    class Meta:
        model = apps.get_model("blog", "BlogCommentTranslation")
        django_get_or_create = ("language_code", "master")


@factory.django.mute_signals(signals.m2m_changed)
class BlogCommentFactory(factory.django.DjangoModelFactory):
    approved = factory.Faker("pybool", truth_probability=75)
    user = factory.LazyFunction(get_or_create_user)
    post = factory.LazyFunction(get_or_create_post)
    parent = None

    class Meta:
        model = BlogComment
        django_get_or_create = ("user", "post")
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            BlogCommentTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
