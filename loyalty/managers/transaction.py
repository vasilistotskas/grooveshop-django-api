from __future__ import annotations

from typing import Self

from django.db.models import CharField, Exists, OuterRef, QuerySet, Sum, Value
from django.db.models.functions import Cast, Concat

from core.managers import OptimizedManager, OptimizedQuerySet
from loyalty.enum import TransactionType


class PointsTransactionQuerySet(OptimizedQuerySet):
    def for_user(self, user) -> Self:
        return self.filter(user=user)

    def get_balance(self, user) -> int:
        """Sum all points for a user."""
        result = self.filter(user=user).aggregate(balance=Sum("points"))
        return result["balance"] or 0

    def get_earn_transactions_for_order(self, order) -> QuerySet:
        """Get all EARN transactions for a specific order."""
        return self.filter(
            reference_order=order, transaction_type=TransactionType.EARN
        )

    def get_expirable_transactions(self, cutoff_date) -> QuerySet:
        """Get EARN transactions older than cutoff that haven't been expired yet.

        Uses a database-level Exists subquery to exclude EARN transactions
        that already have a corresponding EXPIRE transaction, preventing
        duplicate expirations when this runs multiple times.
        """
        # Subquery: check if an EXPIRE tx exists whose description
        # matches "Points expired from transaction {earn_tx.id}"
        has_expire = self.model.objects.filter(
            transaction_type=TransactionType.EXPIRE,
            description=Concat(
                Value("Points expired from transaction "),
                Cast(OuterRef("pk"), output_field=CharField()),
            ),
        )

        return self.filter(
            transaction_type=TransactionType.EARN,
            created_at__lt=cutoff_date,
            points__gt=0,
        ).exclude(Exists(has_expire))

    def has_earn_transactions(self, user) -> bool:
        """Check if user has any EARN transactions (for new customer bonus check)."""
        return self.filter(
            user=user, transaction_type=TransactionType.EARN
        ).exists()

    def for_list(self) -> Self:
        return self.select_related("user", "reference_order", "created_by")

    def for_detail(self) -> Self:
        return self.for_list()


class PointsTransactionManager(OptimizedManager):
    queryset_class = PointsTransactionQuerySet
