from datetime import timedelta

from django.utils import timezone
from parler.managers import TranslatableManager, TranslatableQuerySet


class OrderHistoryQuerySet(TranslatableQuerySet):
    def for_order(self, order):
        return self.filter(order=order)

    def by_change_type(self, change_type):
        return self.filter(change_type=change_type)

    def by_user(self, user):
        return self.filter(user=user)

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def status_changes(self):
        return self.filter(change_type="STATUS")

    def payment_changes(self):
        return self.filter(change_type="PAYMENT")

    def system_changes(self):
        return self.filter(user__isnull=True)

    def user_changes(self):
        return self.filter(user__isnull=False)


class OrderHistoryManager(TranslatableManager):
    def get_queryset(self) -> OrderHistoryQuerySet:
        return OrderHistoryQuerySet(self.model, using=self._db)

    def for_order(self, order):
        return self.get_queryset().for_order(order)

    def by_change_type(self, change_type):
        return self.get_queryset().by_change_type(change_type)

    def by_user(self, user):
        return self.get_queryset().by_user(user)

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def status_changes(self):
        return self.get_queryset().status_changes()

    def payment_changes(self):
        return self.get_queryset().payment_changes()

    def system_changes(self):
        return self.get_queryset().system_changes()

    def user_changes(self):
        return self.get_queryset().user_changes()


class OrderItemHistoryQuerySet(TranslatableQuerySet):
    def for_order_item(self, order_item):
        return self.filter(order_item=order_item)

    def for_order(self, order):
        return self.filter(order_item__order=order)

    def by_change_type(self, change_type):
        return self.filter(change_type=change_type)

    def quantity_changes(self):
        return self.filter(change_type="QUANTITY")

    def price_changes(self):
        return self.filter(change_type="PRICE")

    def refunds(self):
        return self.filter(change_type="REFUND")


class OrderItemHistoryManager(TranslatableManager):
    def get_queryset(self) -> OrderItemHistoryQuerySet:
        return OrderItemHistoryQuerySet(self.model, using=self._db)

    def for_order_item(self, order_item):
        return self.get_queryset().for_order_item(order_item)

    def for_order(self, order):
        return self.get_queryset().for_order(order)

    def by_change_type(self, change_type):
        return self.get_queryset().by_change_type(change_type)

    def quantity_changes(self):
        return self.get_queryset().quantity_changes()

    def price_changes(self):
        return self.get_queryset().price_changes()

    def refunds(self):
        return self.get_queryset().refunds()
