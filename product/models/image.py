from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.fields.image import ImageAndSvgField
from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from core.utils.image_paths import image_to_media_path
from product.managers.image import ProductImageManager


class ProductImage(
    TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel
):
    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="images",
        on_delete=models.CASCADE,
    )
    image = ImageAndSvgField(_("Image"), upload_to="uploads/products/")
    is_main = models.BooleanField(_("Is Main"), default=False)
    translations = TranslatedFields(
        title=models.CharField(_("Title"), max_length=50, blank=True, null=True)
    )

    objects: ProductImageManager = ProductImageManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Product Image")
        verbose_name_plural = _("Product Images")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(
                fields=["product", "is_main"],
                name="prod_img_product_is_main_ix",
            ),
        ]

    def __str__(self):
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        main_status = "Main" if self.is_main else "Secondary"
        return f"{product_name} Image ({main_status})"

    def get_ordering_queryset(self):
        return self.product.images.all()

    def save(self, *args, **kwargs):
        from django.db import transaction

        with transaction.atomic():
            if self.is_main and self.product_id:
                ProductImage.objects.filter(
                    product_id=self.product_id, is_main=True
                ).exclude(pk=self.pk).update(is_main=False)

            super().save(*args, **kwargs)

    @property
    def main_image_path(self) -> str:
        # Returns ``media/{schema}/uploads/products/{filename}`` under
        # TenantFileSystemStorage so media-stream + Nuxt can resolve
        # the file on any tenant. See core.utils.image_paths.
        return image_to_media_path(self.image)
