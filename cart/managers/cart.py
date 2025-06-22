from datetime import timedelta

from django.db import models
from django.utils import timezone
from extra_settings.models import Setting


class CartQuerySet(models.QuerySet):
    def active(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(last_activity__gte=cutoff_time).exclude(
            items__isnull=True
        )

    def abandoned(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(last_activity__lt=cutoff_time)

    def empty(self):
        return self.filter(items__isnull=True)

    def with_items(self):
        return self.filter(items__isnull=False)

    def for_user(self, user):
        if user.is_authenticated:
            return self.filter(user=user)
        return self.none()

    def guest_carts(self):
        return self.filter(user__isnull=True)

    def user_carts(self):
        return self.filter(user__isnull=False)

    def with_totals(self):
        return self.prefetch_related("items__product").annotate(
            total_quantity=models.Sum("items__quantity"),
            unique_items_count=models.Count("items", distinct=True),
        )

    def expired(self, days=30):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(last_activity__lt=cutoff_date)

    def by_date_range(self, start_date, end_date):
        return self.filter(created_at__date__range=[start_date, end_date])

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_country(self, country_code):
        return self.filter(user__country__alpha_2=country_code)

    def with_specific_product(self, product):
        return self.filter(items__product=product).distinct()


class CartManager(models.Manager):
    def get_queryset(self) -> CartQuerySet:
        return CartQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def abandoned(self):
        return self.get_queryset().abandoned()

    def empty(self):
        return self.get_queryset().empty()

    def with_items(self):
        return self.get_queryset().with_items()

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def guest_carts(self):
        return self.get_queryset().guest_carts()

    def user_carts(self):
        return self.get_queryset().user_carts()

    def with_totals(self):
        return self.get_queryset().with_totals()

    def expired(self, days=30):
        return self.get_queryset().expired(days)

    def by_date_range(self, start_date, end_date):
        return self.get_queryset().by_date_range(start_date, end_date)

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def by_country(self, country_code):
        return self.get_queryset().by_country(country_code)

    def with_specific_product(self, product):
        return self.get_queryset().with_specific_product(product)

    def cleanup_expired(self, days=30):
        expired_carts = self.expired(days)
        count = expired_carts.count()
        expired_carts.delete()
        return count
