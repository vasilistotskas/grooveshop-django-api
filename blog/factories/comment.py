import factory
from django.apps import apps
from django.conf import settings

from blog.models.comment import BlogComment

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class BlogCommentTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    content = factory.Faker("paragraph")
    master = factory.SubFactory("blog.factories.comment.BlogCommentFactory")

    class Meta:
        model = apps.get_model("blog", "BlogCommentTranslation")
        django_get_or_create = ("language_code", "master")


class BlogCommentFactory(factory.django.DjangoModelFactory):
    is_approved = factory.Faker("boolean")
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    post = factory.SubFactory("blog.factories.post.BlogPostFactory")
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
            BlogCommentTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
