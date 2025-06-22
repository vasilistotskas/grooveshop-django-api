import factory
from django.core.files.base import ContentFile
from factory import fuzzy
from factory.django import DjangoModelFactory

from product.enum.category import CategoryImageTypeEnum
from product.factories.category import ProductCategoryFactory
from product.models.category_image import ProductCategoryImage


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

    image = factory.LazyAttribute(
        lambda obj: ContentFile(
            factory.django.ImageField()._make_data(
                {"width": 300, "height": 200, "color": "blue"}
            ),
            name=f"category_{obj.category.id}_{obj.image_type}.jpg",
        )
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

        if extracted:
            for lang_code, translation_data in extracted.items():
                self.create_translation(
                    language_code=lang_code,
                    title=translation_data.get(
                        "title", f"Category Image {self.id}"
                    ),
                    alt_text=translation_data.get(
                        "alt_text", f"Alt text for {self.id}"
                    ),
                )
        else:
            self.create_translation(
                language_code="en",
                title=f"Category Image {self.id}",
                alt_text=f"Alt text for category image {self.id}",
            )

        # Manual save since we disabled automatic postgeneration save
        self.save()
