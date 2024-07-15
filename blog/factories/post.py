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
        exclude = ("unique_model_fields", "num_tags", "num_comments")

    num_tags = factory.LazyAttribute(lambda o: 2)
    num_comments = factory.LazyAttribute(lambda o: 10)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        num_tags = kwargs.pop("num_tags", 2)
        num_comments = kwargs.pop("num_comments", 10)
        instance = super()._create(model_class, *args, **kwargs)

        if "create" in kwargs and kwargs["create"]:
            if num_tags > 0:
                tags = BlogTagFactory.create_batch(num_tags)
                instance.tags.add(*tags)
            if num_comments > 0:
                BlogCommentFactory.create_batch(num_comments, post=instance)

        return instance

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
