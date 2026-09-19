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

    def visible_to(self, user) -> Self:
        """Public visibility gate for the read-only category endpoints.

        Staff see everything (admin preview); everyone else sees only a
        branch that is active the whole way up. Every public entry point
        into categories must route through this — the storefront reads
        the tree from three of them (the header menu, the products-page
        nav, the filter sidebar), the sitemap from a fourth and the
        agent gateway's catalogue feed from a fifth, and until
        2026-09-19 none of them filtered: a category switched off in the
        admin kept its crawlable pill on ``/products``, its row in the
        filter tree and its entry in the sitemap, linking to a page that
        listed nothing.

        An INACTIVE ANCESTOR hides its descendants too. Filtering on the
        row's own flag alone leaves a half-hidden branch: children whose
        parent is not in the payload vanish from the menu (it indexes by
        parent id) but keep their pill on the products page and their
        sitemap entry, so "switch off Charging" would hide the heading
        and leave its four subcategories loose. The ancestor test is an
        MPTT containment check — same tree, ``lft`` before and ``rght``
        after — which is one correlated EXISTS rather than a walk.
        """
        from tenant.membership import is_store_staff

        if is_store_staff(user):
            return self
        return self.active_branch()

    def active_branch(self) -> Self:
        """Active rows that have no inactive ancestor."""
        from django.db.models import Exists, OuterRef

        from product.models.category import ProductCategory

        inactive_ancestors = ProductCategory.objects.filter(
            active=False,
            tree_id=OuterRef("tree_id"),
            lft__lt=OuterRef("lft"),
            rght__gt=OuterRef("rght"),
        )
        return self.filter(active=True).exclude(Exists(inactive_ancestors))

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
