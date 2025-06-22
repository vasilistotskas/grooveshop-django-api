from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from mptt.fields import TreeForeignKey
from mptt.models import MPTTModel
from parler.models import TranslatableModel, TranslatedFields
from tinymce.models import HTMLField

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from core.utils.generators import SlugifyConfig, unique_slugify
from product.enum.category import CategoryImageTypeEnum
from product.managers.category import CategoryManager
from product.models.product import Product
from seo.models import SeoModel


class ProductCategory(
    TranslatableModel,
    SortableModel,
    MPTTModel,
    UUIDModel,
    TimeStampMixinModel,
    SeoModel,
):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    active = models.BooleanField(_("Active"), default=True)
    parent = TreeForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=255, blank=True, null=True),
        description=HTMLField(_("Description"), blank=True, null=True),
    )

    objects: CategoryManager = CategoryManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Category")
        verbose_name_plural = _("Product Categories")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["slug"], name="product_category_slug_ix"),
            BTreeIndex(fields=["parent"], name="product_category_parent_ix"),
        ]

    class MPTTMeta:
        order_insertion_by = ["sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sub_categories_list = None

    def __str__(self):
        if not hasattr(self, "_full_path"):
            self._full_path = " / ".join(
                [
                    k.safe_translation_getter("name", any_language=True)
                    for k in self.get_ancestors(include_self=True)
                ]
            )
        return self._full_path

    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(instance=self, title_field="name")
            self.slug = unique_slugify(config)
        super().save(*args, **kwargs)

    def get_ordering_queryset(self):
        return ProductCategory.objects.filter(
            parent=self.parent
        ).get_descendants(include_self=True)

    @property
    def recursive_product_count(self) -> int:
        return Product.objects.filter(
            category__in=self.get_descendants(include_self=True)
        ).count()

    @property
    def main_image(self):
        from product.models.category_image import ProductCategoryImage  # noqa: PLC0415, I001

        return ProductCategoryImage.get_main_image(self)

    @property
    def banner_image(self):
        from product.models.category_image import ProductCategoryImage  # noqa: PLC0415, I001

        return ProductCategoryImage.get_banner_image(self)

    @property
    def icon_image(self):
        from product.models.category_image import ProductCategoryImage  # noqa: PLC0415, I001

        return ProductCategoryImage.get_icon_image(self)

    @property
    def main_image_path(self) -> str:
        main_img = self.main_image
        return main_img.image_path if main_img else ""

    @property
    def banner_image_path(self) -> str:
        banner_img = self.banner_image
        return banner_img.image_path if banner_img else ""

    @property
    def icon_image_path(self) -> str:
        icon_img = self.icon_image
        return icon_img.image_path if icon_img else ""

    @property
    def main_image_url(self) -> str:
        main_img = self.main_image
        return main_img.image_url if main_img else ""

    @property
    def banner_image_url(self) -> str:
        banner_img = self.banner_image
        return banner_img.image_url if banner_img else ""

    @property
    def icon_image_url(self) -> str:
        icon_img = self.icon_image
        return icon_img.image_url if icon_img else ""

    @property
    def category_menu_image_one_path(self) -> str:
        return self.main_image_path

    @property
    def category_menu_image_two_path(self) -> str:
        return self.banner_image_path

    @property
    def category_menu_main_banner_path(self) -> str:
        return self.banner_image_path

    def get_image_by_type(self, image_type: CategoryImageTypeEnum):
        from product.models.category_image import ProductCategoryImage  # noqa: PLC0415, I001

        return ProductCategoryImage.get_image_by_type(self, image_type)

    def get_all_images(self):
        return self.images.active()
