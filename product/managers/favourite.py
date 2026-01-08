from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from typing import Self


class FavouriteQuerySet(models.QuerySet):
    """
    Optimized QuerySet for ProductFavourite model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def with_product(self) -> Self:
        """Select related product with translations."""
        return self.select_related("product").prefetch_related(
            "product__translations"
        )

    def with_product_images(self) -> Self:
        """Prefetch product images."""
        return self.prefetch_related("product__images__translations")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes user and product with translations.
        """
        return self.with_user().with_product()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Includes everything from for_list() plus product images.
        """
        return self.for_list().with_product_images()

    def by_user(self, user_id: int):
        return self.filter(user_id=user_id)

    def by_product(self, product_id: int):
        return self.filter(product_id=product_id)

    def for_user(self, user):
        return self.filter(user=user)

    def for_product(self, product):
        return self.filter(product=product)


class FavouriteManager(models.Manager):
    """
    Manager for ProductFavourite model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return ProductFavourite.objects.for_list()
            return ProductFavourite.objects.for_detail()
    """

    def get_queryset(self) -> FavouriteQuerySet:
        return FavouriteQuerySet(self.model, using=self._db)

    def for_list(self) -> FavouriteQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> FavouriteQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def by_user(self, user_id: int):
        return self.get_queryset().by_user(user_id)

    def by_product(self, product_id: int):
        return self.get_queryset().by_product(product_id)

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def for_product(self, product):
        return self.get_queryset().for_product(product)
