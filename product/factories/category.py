import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import CustomDjangoModelFactory
from product.models.category import ProductCategory

fake = Faker()

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class ProductCategoryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    description = factory.Faker("paragraph")

    class Meta:
        model = apps.get_model("product", "ProductCategoryTranslation")


class ProductCategoryFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    parent = None

    class Meta:
        model = ProductCategory
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if extracted is not None:
            if hasattr(self, "translations"):
                self.translations.all().delete()

            translations = extracted
            for translation in translations:
                translation.master = self
                translation.save()
            return

        if hasattr(self, "translations"):
            existing_translations = self.translations.all()
            needs_translations = not existing_translations.exists() or all(
                not t.name and not t.description for t in existing_translations
            )

            if needs_translations:
                existing_translations.delete()

                for lang in available_languages:
                    ProductCategoryTranslationFactory.create(
                        language_code=lang, master=self
                    )
