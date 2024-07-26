import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from blog.enum.blog_post_enum import PostStatusEnum
from blog.factories.comment import BlogCommentFactory
from blog.factories.tag import BlogTagFactory
from blog.models.post import BlogPost
from core.factories import CustomDjangoModelFactory

fake = Faker()

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class BlogPostTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("sentence", nb_words=6)
    subtitle = factory.Faker("sentence", nb_words=12)
    body = factory.Faker("paragraph", nb_sentences=5)
    search_document = factory.Faker("text")
    master = factory.SubFactory("blog.factories.post.BlogPostFactory")

    class Meta:
        model = apps.get_model("blog", "BlogPostTranslation")
        django_get_or_create = ("language_code", "master")


class BlogPostFactory(CustomDjangoModelFactory):
    slug = factory.LazyFunction(lambda: fake.slug())
    image = factory.django.ImageField(
        filename="blog_image.jpg",
        color=factory.Faker("color"),
        width=1280,
        height=720,
    )
    category = factory.SubFactory("blog.factories.category.BlogCategoryFactory")
    author = factory.SubFactory("blog.factories.author.BlogAuthorFactory")
    status = factory.Iterator(PostStatusEnum.choices, getter=lambda x: x[0])
    featured = factory.Faker("boolean")
    view_count = factory.Faker("random_int", min=0, max=1000)

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
            BlogPostTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
