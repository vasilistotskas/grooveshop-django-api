import os

from django.conf import settings
from django.db import models
from mptt.fields import TreeForeignKey
from mptt.models import MPTTModel
from tinymce.models import HTMLField

from core.models import TimeStampMixinModel
from core.models import UUIDModel
from product.models.product import Product
from seo.models import SeoModel


class ProductCategory(MPTTModel, UUIDModel, TimeStampMixinModel, SeoModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(unique=True)
    description = HTMLField(null=True, blank=True)
    menu_image_one = models.ImageField(
        upload_to="uploads/categories/", null=True, blank=True
    )
    menu_image_two = models.ImageField(
        upload_to="uploads/categories/", null=True, blank=True
    )
    menu_main_banner = models.ImageField(
        upload_to="uploads/categories/", null=True, blank=True
    )
    parent = TreeForeignKey(
        "self", blank=True, null=True, related_name="children", on_delete=models.CASCADE
    )

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ("id",)

    class MPTTMeta:
        order_insertion_by: list[str] = ["name"]

    def __init__(self, *args, **kwargs):
        super(ProductCategory, self).__init__(*args, **kwargs)
        self.sub_categories_list = None

    def __str__(self):
        full_path: list[str] = [self.name]
        k = self.parent
        while k is not None:
            full_path.append(k.name)
            k = k.parent
        return " / ".join(full_path[::-1])

    @property
    def recursive_product_count(self) -> int:
        return Product.objects.filter(
            category__in=self.get_descendants(include_self=True)
        ).count()

    @property
    def absolute_url(self) -> str:
        return "/" + "/".join(
            [x["slug"] for x in self.get_ancestors(include_self=True).values()]
        )

    @property
    def category_menu_image_one_absolute_url(self) -> str:
        image: str = ""
        if self.menu_image_one:
            return settings.BACKEND_BASE_URL + self.menu_image_one.url
        return image

    @property
    def category_menu_image_one_filename(self) -> str:
        if self.menu_image_one:
            return os.path.basename(self.menu_image_one.name)
        else:
            return ""

    @property
    def category_menu_image_two_absolute_url(self) -> str:
        image: str = ""
        if self.menu_image_two:
            image = settings.BACKEND_BASE_URL + self.menu_image_two.url
        return image

    @property
    def category_menu_image_two_filename(self) -> str:
        if self.menu_image_two:
            return os.path.basename(self.menu_image_two.name)
        else:
            return ""

    @property
    def category_menu_main_banner_absolute_url(self) -> str:
        image: str = ""
        if self.menu_main_banner:
            return settings.BACKEND_BASE_URL + self.menu_main_banner.url
        return image

    @property
    def category_menu_main_banner_filename(self) -> str:
        if self.menu_main_banner:
            return os.path.basename(self.menu_main_banner.name)
        else:
            return ""
