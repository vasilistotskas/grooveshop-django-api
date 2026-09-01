from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import TreeTranslatableManager, TreeTranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class CategoryQuerySet(TreeTranslatableQuerySet):
    """
    Optimized QuerySet for ProductCategory model.

    Combines Parler translations with MPTT tree structure.
    """

    def active(self) -> Self:
        return self.filter(active=True)

    def with_products_count(self) -> Self:
        """Annotate with products count."""
        from django.db.models import Count

        return self.annotate(_products_count=Count("products", distinct=True))

    def with_main_image(self) -> Self:
        """Prefetch only the MAIN image to avoid N+1 in main_image_path.

        Mirrors ``ProductQuerySet.with_main_image``. The filter matches
        ``CategoryImageQuerySet.get_main_image`` exactly — image_type
        MAIN and active — or the prefetched list would disagree with the
        non-prefetched fallback.
        """
        from django.db.models import Prefetch

        from product.enum.category import CategoryImageTypeEnum
        from product.models.category_image import ProductCategoryImage

        return self.prefetch_related(
            Prefetch(
                "images",
                queryset=ProductCategoryImage.objects.filter(
                    image_type=CategoryImageTypeEnum.MAIN, active=True
                ),
                to_attr="_prefetched_main_images",
            )
        )

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations, parent and the main image.
        """
        return self.with_translations().with_parent().with_main_image()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus products count.
        """
        return self.for_list().with_products_count()


class CategoryManager(TreeTranslatableManager):
    """
    Manager for ProductCategory model with optimized queryset methods.
    """

    queryset_class = CategoryQuerySet

    def get_queryset(self) -> CategoryQuerySet:
        return self.queryset_class(self.model, using=self._db)

    def for_list(self) -> CategoryQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> CategoryQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
