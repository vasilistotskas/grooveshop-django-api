from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Prefetch

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class FavouriteQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for ProductFavourite model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_user(self) -> Self:
        """Select related user."""
        return self.select_related("user")

    def _with_enriched_product(self) -> Self:
        """Prefetch ``product`` with everything the embedded
        ``ProductDetailSerializer`` renders — translations, category/vat/
        brand, review/likes counts, images and attributes — so serializing N
        favourites stays O(1) queries (G0301).

        Composed from the Product optimizers directly rather than
        ``Product.objects.for_detail()`` to avoid coupling to its
        deleted/active filtering; a favourite can point at any product.
        """
        from product.models.product import Product

        product_qs = (
            Product.objects.with_translations()
            .with_category()
            .with_counts()
            .with_main_image()
            .with_product_attributes()
        )
        return self.prefetch_related(Prefetch("product", queryset=product_qs))

    def for_list(self) -> Self:
        """Optimized queryset for list views: user + fully enriched product
        (the list serializer embeds the detail-tier product)."""
        return self.with_user()._with_enriched_product()

    def for_detail(self) -> Self:
        """Optimized queryset for detail views (same enrichment as list)."""
        return self.with_user()._with_enriched_product()

    def by_user(self, user_id: int) -> Self:
        """Filter by user ID."""
        return self.filter(user_id=user_id)

    def by_product(self, product_id: int) -> Self:
        """Filter by product ID."""
        return self.filter(product_id=product_id)

    def for_user(self, user) -> Self:
        """Filter by user instance."""
        return self.filter(user=user)

    def for_product(self, product) -> Self:
        """Filter by product instance."""
        return self.filter(product=product)


class FavouriteManager(OptimizedManager):
    """
    Manager for ProductFavourite model with optimized queryset methods.

    Most methods are automatically delegated to FavouriteQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return ProductFavourite.objects.for_list()
            return ProductFavourite.objects.for_detail()
    """

    queryset_class = FavouriteQuerySet

    def get_queryset(self) -> FavouriteQuerySet:
        """Return the base queryset."""
        return FavouriteQuerySet(self.model, using=self._db)

    def for_list(self) -> FavouriteQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> FavouriteQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
