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


class CartItemQuerySet(models.QuerySet):
    def for_cart(self, cart):
        return self.filter(cart=cart)

    def for_product(self, product):
        return self.filter(product=product)

    def for_user(self, user):
        if user.is_authenticated:
            return self.filter(cart__user=user)
        return self.none()

    def with_product_data(self):
        return self.select_related("product", "cart")

    def total_quantity(self):
        return self.aggregate(total=models.Sum("quantity"))["total"] or 0

    def by_product_popularity(self):
        return (
            self.values("product")
            .annotate(
                total_quantity=models.Sum("quantity"),
                cart_count=models.Count("cart", distinct=True),
            )
            .order_by("-total_quantity", "-cart_count")
        )

    def high_quantity(self, threshold=5):
        return self.filter(quantity__gte=threshold)

    def recent(self, days=7):
        cutoff_date = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=cutoff_date)

    def by_date_range(self, start_date, end_date):
        return self.filter(created_at__date__range=[start_date, end_date])

    def by_quantity_range(self, min_qty, max_qty):
        return self.filter(quantity__range=[min_qty, max_qty])

    def by_price_range(self, min_price, max_price):
        return self.filter(product__price__amount__range=[min_price, max_price])

    def expensive_items(self, threshold=100):
        return self.filter(product__price__amount__gte=threshold)

    def in_active_carts(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(cart__last_activity__gte=cutoff_time)

    def in_abandoned_carts(self):
        abandoned_threshold = Setting.get("CART_ABANDONED_HOURS", default=24)
        cutoff_time = timezone.now() - timedelta(hours=abandoned_threshold)
        return self.filter(cart__last_activity__lt=cutoff_time)

    def with_discounts(self):
        return self.filter(product__discount_percent__gt=0)


class CartItemManager(models.Manager):
    def get_queryset(self) -> CartItemQuerySet:
        return CartItemQuerySet(self.model, using=self._db)

    def for_cart(self, cart):
        return self.get_queryset().for_cart(cart)

    def for_product(self, product):
        return self.get_queryset().for_product(product)

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def with_product_data(self):
        return self.get_queryset().with_product_data()

    def total_quantity(self):
        return self.get_queryset().total_quantity()

    def by_product_popularity(self):
        return self.get_queryset().by_product_popularity()

    def high_quantity(self, threshold=5):
        return self.get_queryset().high_quantity(threshold)

    def recent(self, days=7):
        return self.get_queryset().recent(days)

    def by_date_range(self, start_date, end_date):
        return self.get_queryset().by_date_range(start_date, end_date)

    def by_quantity_range(self, min_qty, max_qty):
        return self.get_queryset().by_quantity_range(min_qty, max_qty)

    def by_price_range(self, min_price, max_price):
        return self.get_queryset().by_price_range(min_price, max_price)

    def expensive_items(self, threshold=100):
        return self.get_queryset().expensive_items(threshold)

    def in_active_carts(self):
        return self.get_queryset().in_active_carts()

    def in_abandoned_carts(self):
        return self.get_queryset().in_abandoned_carts()

    def with_discounts(self):
        return self.get_queryset().with_discounts()
