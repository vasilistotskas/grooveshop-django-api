import uuid
import factory
from django.apps import apps
from django.conf import settings
from factory import fuzzy
from factory.django import DjangoModelFactory

from product.enum.category import CategoryImageTypeEnum
from product.factories.category import ProductCategoryFactory
from product.models.category_image import ProductCategoryImage

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class ProductCategoryImageTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("word")
    alt_text = factory.Faker("sentence")
    master = factory.SubFactory(
        "product.factories.category_image.ProductCategoryImageFactory"
    )

    class Meta:
        model = apps.get_model("product", "ProductCategoryImageTranslation")
        django_get_or_create = ("language_code", "master")


class ProductCategoryImageFactory(DjangoModelFactory):
    class Meta:
        model = ProductCategoryImage
        skip_postgeneration_save = True

    category = factory.SubFactory(ProductCategoryFactory)
    image_type = fuzzy.FuzzyChoice(
        CategoryImageTypeEnum.choices, getter=lambda c: c[0]
    )
    active = True
    sort_order = factory.Sequence(lambda n: n)

    image = factory.django.ImageField(
        filename=factory.LazyFunction(
            lambda: f"category_{uuid.uuid4().hex[:8]}.jpg"
        ),
        width=300,
        height=200,
        color="blue",
    )

    class Params:
        main_image = factory.Trait(
            image_type=CategoryImageTypeEnum.MAIN,
            sort_order=1,
        )
        banner_image = factory.Trait(
            image_type=CategoryImageTypeEnum.BANNER,
            sort_order=2,
        )
        icon_image = factory.Trait(
            image_type=CategoryImageTypeEnum.ICON,
            sort_order=3,
        )
        inactive = factory.Trait(active=False)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductCategoryImageTranslationFactory(
                language_code=lang, master=self
            )
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
