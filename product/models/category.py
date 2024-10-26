import os
from typing import override

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from mptt.fields import TreeForeignKey
from mptt.managers import TreeManager
from mptt.models import MPTTModel
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager
from parler.managers import TranslatableQuerySet
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify
from product.models.product import Product
from seo.models import SeoModel


class CategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    @override
    def as_manager(cls):
        # make sure creating managers from querysets works.
        manager = CategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True
    as_manager = classmethod(as_manager)


class CategoryManager(TreeManager, TranslatableManager):
    _queryset_class = CategoryQuerySet


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
        ]

    class MPTTMeta:
        order_insertion_by = ["sort_order"]

    def __init__(self, *args, **kwargs):
        super(ProductCategory, self).__init__(*args, **kwargs)
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

    @override
    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(instance=self, title_field="name")
            self.slug = unique_slugify(config)
        super(ProductCategory, self).save(*args, **kwargs)

    @override
    def get_ordering_queryset(self):
        return ProductCategory.objects.filter(parent=self.parent).get_descendants(include_self=True)

    @property
    def recursive_product_count(self) -> int:
        return Product.objects.filter(category__in=self.get_descendants(include_self=True)).count()

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
