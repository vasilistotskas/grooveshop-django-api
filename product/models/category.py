from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from mptt.fields import TreeForeignKey
from mptt.models import MPTTModel
from parler.models import TranslatableModel, TranslatedFields
from tinymce.models import HTMLField

from core.models import SeoModel, SortableModel, TimeStampMixinModel, UUIDModel
from core.utils.generators import SlugifyConfig, unique_slugify
from product.managers.category import CategoryManager
from product.models.product import Product


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
        return self.safe_translation_getter("name") or ""

    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(instance=self, title_field="name")
            self.slug = unique_slugify(config)
        super().save(*args, **kwargs)

    def get_ordering_queryset(self):
        # SortableModel orders items WITHIN a scope; for a tree that scope is
        # the direct siblings (same parent). Spanning descendants too made
        # move_up/move_down and SortableModel.delete() renumber unrelated
        # subtree nodes, corrupting sort_order (G0310).
        return ProductCategory.objects.filter(parent=self.parent)

    @property
    def recursive_product_count(self) -> int:
        return Product.objects.filter(
            category__in=self.get_descendants(include_self=True)
        ).count()

    @property
    def main_image(self):
        from product.models.category_image import ProductCategoryImage  # noqa: I001

        # Use the prefetched main image when the queryset supplied one
        # (CategoryQuerySet.with_main_image, mirroring Product) — without
        # it, serializing a category LIST costs one query per row.
        prefetched = getattr(self, "_prefetched_main_images", None)
        if prefetched is not None:
            return prefetched[0] if prefetched else None
        return ProductCategoryImage.get_main_image(self)

    @property
    def main_image_path(self) -> str:
        main_img = self.main_image
        return main_img.image_path if main_img else ""
