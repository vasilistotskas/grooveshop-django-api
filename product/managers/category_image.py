from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from product.enum.category import CategoryImageTypeEnum

if TYPE_CHECKING:
    from typing import Self


class CategoryImageQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for CategoryImage model.

    Provides chainable methods for filtering and retrieving category images.
    """

    def active(self) -> Self:
        """Filter active images."""
        return self.filter(active=True)

    def by_category(self, category) -> Self:
        """Filter by category."""
        return self.filter(category=category)

    def by_type(self, image_type: CategoryImageTypeEnum) -> Self:
        """Filter by image type."""
        return self.filter(image_type=image_type)

    def main_images(self) -> Self:
        """Filter main images."""
        return self.filter(image_type=CategoryImageTypeEnum.MAIN)

    def banner_images(self) -> Self:
        """Filter banner images."""
        return self.filter(image_type=CategoryImageTypeEnum.BANNER)

    def icon_images(self) -> Self:
        """Filter icon images."""
        return self.filter(image_type=CategoryImageTypeEnum.ICON)

    def gallery_images(self) -> Self:
        """Filter gallery images."""
        return self.filter(image_type=CategoryImageTypeEnum.GALLERY)

    def get_main_image(self, category):
        """Get the main image for a category."""
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.MAIN,
            active=True,
        ).first()

    def get_banner_image(self, category):
        """Get the banner image for a category."""
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        ).first()

    def get_icon_image(self, category):
        """Get the icon image for a category."""
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.ICON,
            active=True,
        ).first()

    def get_image_by_type(self, category, image_type: CategoryImageTypeEnum):
        """Get an image by category and type."""
        return self.filter(
            category=category, image_type=image_type, active=True
        ).first()

    def for_list(self) -> Self:
        """Return optimized queryset for list views."""
        return self.with_translations().select_related("category")

    def for_detail(self) -> Self:
        """Return optimized queryset for detail views."""
        return self.for_list()


class CategoryImageManager(TranslatableOptimizedManager):
    """
    Manager for CategoryImage model.

    Most methods are automatically delegated to CategoryImageQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.
    """

    queryset_class = CategoryImageQuerySet

    def get_queryset(self) -> CategoryImageQuerySet:
        """Return the base queryset."""
        return CategoryImageQuerySet(self.model, using=self._db)

    def for_list(self) -> CategoryImageQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CategoryImageQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
