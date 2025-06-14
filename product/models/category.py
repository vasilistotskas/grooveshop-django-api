import os

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
    menu_image_one = models.ImageField(
        _("Menu Image One"),
        upload_to="uploads/categories/",
        null=True,
        blank=True,
    )
    menu_image_two = models.ImageField(
        _("Menu Image Two"),
        upload_to="uploads/categories/",
        null=True,
        blank=True,
    )
    menu_main_banner = models.ImageField(
        _("Menu Main Banner"),
        upload_to="uploads/categories/",
        null=True,
        blank=True,
    )
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

    objects = CategoryManager()

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
    def absolute_url(self) -> str:
        return f"/product/category/{self.id}/" + "/".join(
            [x["slug"] for x in self.get_ancestors(include_self=True).values()]
        )

    @property
    def category_menu_image_one_path(self) -> str:
        if self.menu_image_one:
            return f"media/uploads/categories/{os.path.basename(self.menu_image_one.name)}"
        return ""

    @property
    def category_menu_image_two_path(self) -> str:
        if self.menu_image_two:
            return f"media/uploads/categories/{os.path.basename(self.menu_image_two.name)}"
        return ""

    @property
    def category_menu_main_banner_path(self) -> str:
        if self.menu_main_banner:
            return f"media/uploads/categories/{os.path.basename(self.menu_main_banner.name)}"
        return ""
