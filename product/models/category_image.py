from __future__ import annotations

import os

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.fields.image import ImageAndSvgField
from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from product.enum.category import CategoryImageTypeEnum
from product.managers.category_image import CategoryImageManager


class ProductCategoryImage(
    TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel
):
    id = models.BigAutoField(primary_key=True)
    category = models.ForeignKey(
        "product.ProductCategory",
        related_name="images",
        on_delete=models.CASCADE,
    )
    image = ImageAndSvgField(_("Image"), upload_to="uploads/categories/")
    image_type = models.CharField(
        _("Image Type"),
        max_length=20,
        choices=CategoryImageTypeEnum.choices,
        default=CategoryImageTypeEnum.MAIN,
    )
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        title=models.CharField(
            _("Title"), max_length=255, blank=True, null=True
        ),
        alt_text=models.CharField(
            _("Alt Text"), max_length=255, blank=True, null=True
        ),
    )

    objects: CategoryImageManager = CategoryImageManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Category Image")
        verbose_name_plural = _("Product Category Images")
        ordering = ["sort_order"]
        unique_together = [["category", "image_type"]]
        indexes = [
            BTreeIndex(fields=["created_at"], name="cat_img_created_ix"),
            BTreeIndex(fields=["updated_at"], name="cat_img_updated_ix"),
            BTreeIndex(fields=["sort_order"], name="cat_img_sort_ix"),
            BTreeIndex(
                fields=["category", "image_type"],
                name="cat_img_category_type_ix",
            ),
            BTreeIndex(
                fields=["category", "active"],
                name="cat_img_category_active_ix",
            ),
            BTreeIndex(
                fields=["image_type", "active"],
                name="cat_img_type_active_ix",
            ),
        ]

    def __str__(self):
        category_name = self.category.safe_translation_getter(
            "name", any_language=True
        )
        return f"{category_name} - {self.get_image_type_display()}"

    def get_ordering_queryset(self):
        return self.category.images.filter(image_type=self.image_type)

    def clean(self):
        super().clean()

    @property
    def image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return (
                f"media/uploads/categories/{os.path.basename(self.image.name)}"
            )
        return ""

    @property
    def image_url(self) -> str:
        if self.image and hasattr(self.image, "url"):
            return self.image.url
        return ""

    @classmethod
    def get_main_image(cls, category):
        return cls.objects.get_main_image(category)

    @classmethod
    def get_banner_image(cls, category):
        return cls.objects.get_banner_image(category)

    @classmethod
    def get_icon_image(cls, category):
        return cls.objects.get_icon_image(category)

    @classmethod
    def get_image_by_type(cls, category, image_type: CategoryImageTypeEnum):
        return cls.objects.get_image_by_type(category, image_type)
