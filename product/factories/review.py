import factory
from django.apps import apps
from django.conf import settings

from product.enum.review import RateEnum
from product.enum.review import ReviewStatusEnum
from product.models.review import ProductReview

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class ProductReviewTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    comment = factory.Faker("paragraph")
    master = factory.SubFactory("product.factories.review.ProductReviewFactory")

    class Meta:
        model = apps.get_model("product", "ProductReviewTranslation")
        django_get_or_create = ("language_code", "master")


class ProductReviewFactory(factory.django.DjangoModelFactory):
    product = factory.SubFactory("product.factories.product.ProductFactory")
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    rate = factory.Iterator([choice.value for choice in RateEnum])
    status = factory.Iterator([choice.value for choice in ReviewStatusEnum])

    class Meta:
        model = ProductReview
        django_get_or_create = ("product", "user")
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductReviewTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
