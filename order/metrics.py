from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.db import models
from django.db.models import Avg, Count
from django.db.models.functions import (
    TruncDay,
)
from django.utils import timezone

from core.utils.dates import date_range
from order.enum.status_enum import OrderStatusEnum
from order.models.item import OrderItem
from order.models.order import Order


class OrderMetrics:
    @staticmethod
    def get_total_orders(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> int:
        query = Order.objects.all()

        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        return query.count()

    @staticmethod
    def get_total_revenue(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, Decimal]:
        query = Order.objects.filter(status=OrderStatusEnum.COMPLETED.value)

        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        revenue_by_currency = {}
        for order in query:
            if (
                order.total_price_items.currency
                == order.total_price_extra.currency
            ):
                total = (
                    order.total_price_items.amount
                    + order.total_price_extra.amount
                )
                currency = str(order.total_price_items.currency)
            else:
                items_currency = str(order.total_price_items.currency)
                items_amount = order.total_price_items.amount

                extras_currency = str(order.total_price_extra.currency)
                extras_amount = order.total_price_extra.amount

                if items_currency in revenue_by_currency:
                    revenue_by_currency[items_currency] += items_amount
                else:
                    revenue_by_currency[items_currency] = items_amount

                if extras_currency in revenue_by_currency:
                    revenue_by_currency[extras_currency] += extras_amount
                else:
                    revenue_by_currency[extras_currency] = extras_amount

                continue

            if currency in revenue_by_currency:
                revenue_by_currency[currency] += total
            else:
                revenue_by_currency[currency] = total

        return revenue_by_currency

    @staticmethod
    def get_orders_by_status(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, int]:
        query = Order.objects.all()

        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        status_counts = query.values("status").annotate(count=Count("id"))
        return {item["status"]: item["count"] for item in status_counts}

    @staticmethod
    def get_orders_by_day(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        status: Optional[str] = None,
    ) -> dict[str, int]:
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        query = Order.objects.filter(
            created_at__gte=start_date, created_at__lte=end_date
        )

        if status:
            query = query.filter(status=status)

        daily_counts = (
            query.annotate(day=TruncDay("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )

        result = {
            item["day"].strftime("%Y-%m-%d"): item["count"]
            for item in daily_counts
        }

        all_days = date_range(start_date.date(), end_date.date())
        return {
            day.strftime("%Y-%m-%d"): result.get(day.strftime("%Y-%m-%d"), 0)
            for day in all_days
        }

    @staticmethod
    def get_revenue_by_day(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        currency: str = "USD",
    ) -> dict[str, Decimal]:
        if not start_date:
            start_date = timezone.now() - timedelta(days=30)
        if not end_date:
            end_date = timezone.now()

        query = Order.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
            status=OrderStatusEnum.COMPLETED.value,
            paid_amount_currency=currency,
        )

        orders_by_day = {}
        for order in query:
            day = order.created_at.strftime("%Y-%m-%d")
            if day in orders_by_day:
                orders_by_day[day] += order.paid_amount.amount
            else:
                orders_by_day[day] = order.paid_amount.amount

        all_days = date_range(start_date.date(), end_date.date())
        return {
            day.strftime("%Y-%m-%d"): orders_by_day.get(
                day.strftime("%Y-%m-%d"), Decimal("0")
            )
            for day in all_days
        }

    @staticmethod
    def get_avg_order_value(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        currency: str = "USD",
    ) -> Decimal:
        query = Order.objects.filter(
            status=OrderStatusEnum.COMPLETED.value,
            paid_amount_currency=currency,
        )

        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        result = query.aggregate(avg_value=Avg("paid_amount"))
        avg_value = result.get("avg_value", 0)
        return avg_value if avg_value else Decimal("0")

    @staticmethod
    def get_top_selling_products(
        limit: int = 10,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list[dict]:
        query = OrderItem.objects.select_related("product")

        if start_date:
            query = query.filter(order__created_at__gte=start_date)
        if end_date:
            query = query.filter(order__created_at__lte=end_date)

        query = query.filter(order__status=OrderStatusEnum.COMPLETED.value)

        product_sales = (
            query.values(
                "product_id",
            )
            .annotate(
                product_name=models.F("product__product_code"),
                total_quantity=models.Sum("quantity"),
                total_revenue=models.Sum(
                    models.F("price") * models.F("quantity")
                ),
                currency=models.F("price_currency"),
            )
            .order_by("-total_quantity")[:limit]
        )

        return list(product_sales)

    @staticmethod
    def get_conversion_rate(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> float:
        total_orders = OrderMetrics.get_total_orders(start_date, end_date)
        if total_orders == 0:
            return 0.0

        query = Order.objects.filter(status=OrderStatusEnum.COMPLETED.value)
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        completed_orders = query.count()
        return float(completed_orders) / float(total_orders)

    @staticmethod
    def get_order_fulfillment_time(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[timedelta]:
        query = Order.objects.filter(
            status__in=[
                OrderStatusEnum.SHIPPED.value,
                OrderStatusEnum.DELIVERED.value,
                OrderStatusEnum.COMPLETED.value,
            ]
        )

        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        query = query.exclude(status_updated_at__isnull=True)

        durations = [
            (order.status_updated_at - order.created_at) for order in query
        ]
        if not durations:
            return None

        total_seconds = sum(
            (duration.total_seconds() for duration in durations), 0
        )
        avg_seconds = total_seconds / len(durations)
        return timedelta(seconds=avg_seconds)

    @staticmethod
    def get_repeat_customer_rate(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> float:
        query = Order.objects.all()
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        total_customers = query.values("user").distinct().count()
        if total_customers == 0:
            return 0.0

        customer_order_counts = query.values("user").annotate(
            order_count=Count("id")
        )
        repeat_customers = sum(
            1 for item in customer_order_counts if item["order_count"] > 1
        )

        return float(repeat_customers) / float(total_customers)

    @staticmethod
    def get_payment_method_distribution(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> dict[str, int]:
        query = Order.objects.all()
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        payment_counts = query.values("pay_way_id").annotate(count=Count("id"))

        result = {}
        for item in payment_counts:
            pay_way_id = item["pay_way_id"]
            count = item["count"]
            if pay_way_id is not None:
                from pay_way.models import PayWay

                try:
                    pay_way = PayWay.objects.get(id=pay_way_id)
                    pay_way_name = (
                        pay_way.safe_translation_getter(
                            "name", any_language=True
                        )
                        or "Unknown"
                    )
                    result[pay_way_name] = count
                except PayWay.DoesNotExist:
                    result[f"Unknown ({pay_way_id})"] = count
            else:
                result["None"] = count

        return result

    @staticmethod
    def get_order_status_transition_times() -> dict[str, timedelta]:
        return {
            OrderStatusEnum.PENDING.value: timedelta(hours=2),
            OrderStatusEnum.PROCESSING.value: timedelta(hours=8),
            OrderStatusEnum.SHIPPED.value: timedelta(days=3),
            OrderStatusEnum.DELIVERED.value: timedelta(hours=1),
        }

    @staticmethod
    def get_order_cancellation_rate(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> float:
        total_orders = OrderMetrics.get_total_orders(start_date, end_date)
        if total_orders == 0:
            return 0.0

        query = Order.objects.filter(status=OrderStatusEnum.CANCELED.value)
        if start_date:
            query = query.filter(created_at__gte=start_date)
        if end_date:
            query = query.filter(created_at__lte=end_date)

        canceled_orders = query.count()
        return float(canceled_orders) / float(total_orders)
