from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class UserAddressQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for UserAddress model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_location(self) -> Self:
        """Select related country and region."""
        return self.select_related("country", "region")

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes country and region.
        """
        return self.with_location()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this model.
        """
        return self.for_list()

    def for_user(self, user) -> Self:
        """Filter addresses by user."""
        return self.filter(user=user)

    def main_only(self) -> Self:
        """Filter to main addresses only."""
        return self.filter(is_main=True)

    def get_user_addresses(self, user) -> Self:
        """Get all addresses for a user, ordered by main status."""
        return self.for_list().for_user(user).order_by("-is_main", "title")

    def get_main_address(self, user):
        """Get the main address for a user."""
        return self.for_list().for_user(user).main_only().first()


class UserAddressManager(OptimizedManager):
    """
    Manager for UserAddress model with optimized queryset methods.

    Methods not explicitly defined are automatically delegated to
    UserAddressQuerySet via __getattr__.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return UserAddress.objects.for_list()
            return UserAddress.objects.for_detail()
    """

    queryset_class = UserAddressQuerySet

    def get_queryset(self) -> UserAddressQuerySet:
        return UserAddressQuerySet(self.model, using=self._db)

    def for_list(self) -> UserAddressQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> UserAddressQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
