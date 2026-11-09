"""The store-admin agent surface: ``/api/v1/agent/staff/*``.

Read-only views behind the staff MCP server (``/mcp/admin``). Each view
names one scope (``agent/staff.py``), and ``HasStaffScope`` requires both
the scope on the token and the permission behind it on the caller's role.
The figures come from the modules the admin's own pages use
(``order/turnover.py``, ``order/profit.py``), so an AI app and the admin
can never disagree.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import Count, F, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from agent.staff import (
    SCOPE_STAFF_CATALOG_READ,
    SCOPE_STAFF_CUSTOMERS_READ,
    SCOPE_STAFF_MESSAGES_READ,
    SCOPE_STAFF_ORDERS_READ,
    SCOPE_STAFF_PROFIT_READ,
    SCOPE_STAFF_REVIEWS_READ,
    SCOPE_STAFF_SALES_READ,
    STAFF_SCOPE_DISPLAY,
    STAFF_SCOPE_PERMISSIONS,
    role_permission_holds,
)
from agent.staff_auth import (
    HasStaffScope,
    StaffAgentTokenAuthentication,
    granted_scopes,
)
from agent.staff_serializers import (
    StaffCustomerSerializer,
    StaffMeSerializer,
    StaffMessageSerializer,
    StaffOrderDetailSerializer,
    StaffOrderSerializer,
    StaffProductSerializer,
    StaffProfitReportSerializer,
    StaffReportQuerySerializer,
    StaffReviewSerializer,
    StaffSalesReportSerializer,
)
from contact.models import Contact
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import StaffAgentThrottle
from order import profit, turnover
from order.enum.status import OrderStatus, PaymentStatus
from order.models.order import Order
from product.models.product import Product
from product.models.review import ProductReview
from tenant.membership import get_current_tenant, get_membership
from tenant.models import TenantMembershipRole
from tenant.permissions import IsStaffAiAccessEnabled
from user.models import UserAccount

_TAGS = ["Agent staff"]
_UNAUTHORIZED = {401: ErrorResponseSerializer, 403: ErrorResponseSerializer}


def _limit(request: Request) -> int:
    """The ``limit`` query parameter, capped by ``STAFF_AGENT_LIST_LIMIT``."""
    cap = settings.STAFF_AGENT_LIST_LIMIT
    raw = request.query_params.get("limit")
    if raw is None:
        return cap
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(
            {"limit": "Not a whole number."}, code="invalid_limit"
        ) from None
    if value < 1:
        raise ValidationError(
            {"limit": "Must be at least 1."}, code="invalid_limit"
        )
    return min(value, cap)


def _search(request: Request) -> str:
    return (request.query_params.get("search") or "").strip()


_LIMIT_PARAM = OpenApiParameter(
    name="limit",
    type=OpenApiTypes.INT,
    description="How many rows to return; capped by the server.",
)
_SEARCH_PARAM = OpenApiParameter(name="search", type=OpenApiTypes.STR)


class StaffAgentAPIView(APIView):
    """A store-admin view: a platform-issued staff token, one scope.

    Every view first needs the store's ``staff_ai_access`` switch: off,
    the whole surface answers 404, connected apps included.
    """

    authentication_classes = [StaffAgentTokenAuthentication]
    permission_classes = [IsStaffAiAccessEnabled, HasStaffScope]
    throttle_classes = [StaffAgentThrottle]
    staff_scope: str = ""


class StaffAgentMeView(StaffAgentAPIView):
    """Who is connected, to which store, and what the AI app may read.

    Open to any valid staff token whatever its scopes; the gateway reads
    it to verify a token, as it reads ``agent/me`` for a shopper's.
    """

    permission_classes = [IsStaffAiAccessEnabled, IsAuthenticated]

    @extend_schema(
        operation_id="getAgentStaffProfile",
        responses={200: StaffMeSerializer, **_UNAUTHORIZED},
        summary="Connected staff member (store-admin agent surface)",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        tenant = get_current_tenant()
        membership = get_membership(request.user, tenant)
        granted = granted_scopes(request)
        payload = {
            "email": request.user.email,
            "role": membership.role,
            "role_display": TenantMembershipRole(membership.role).label,
            "store_name": tenant.store_name or tenant.name,
            "store_domain": tenant.get_primary_domain().domain,
            "scopes": [
                {
                    "scope": scope,
                    "description": STAFF_SCOPE_DISPLAY[scope],
                    "granted": scope in granted,
                    "allowed": role_permission_holds(request.user, scope),
                }
                for scope in STAFF_SCOPE_PERMISSIONS
            ],
        }
        return Response(StaffMeSerializer(payload).data)


def _order_row(order: Order) -> dict[str, Any]:
    total = order.total_price
    return {
        "id": order.id,
        "uuid": order.uuid,
        "status": order.status,
        "status_display": order.get_status_display(),
        "payment_status": order.payment_status,
        "payment_method": order.pay_way_key or order.payment_method or "",
        "placed_at": order.created_at,
        "shipped_at": order.shipped_at,
        "customer_name": f"{order.first_name} {order.last_name}".strip(),
        "customer_email": order.email,
        "total": total.amount,
        "currency": str(total.currency),
    }


def _amount(money: Any) -> Decimal:
    return money.amount if money is not None else Decimal(0)


class StaffAgentOrdersView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_ORDERS_READ

    @extend_schema(
        operation_id="listAgentStaffOrders",
        parameters=[
            OpenApiParameter(
                name="status", enum=OrderStatus.values, type=OpenApiTypes.STR
            ),
            OpenApiParameter(
                name="paymentStatus",
                enum=PaymentStatus.values,
                type=OpenApiTypes.STR,
            ),
            OpenApiParameter(name="placedFrom", type=OpenApiTypes.DATE),
            OpenApiParameter(name="placedTo", type=OpenApiTypes.DATE),
            _SEARCH_PARAM,
            _LIMIT_PARAM,
        ],
        responses={200: StaffOrderSerializer(many=True), **_UNAUTHORIZED},
        summary="The store's orders, newest first (store-admin agent surface)",
        description=(
            "Filter by status, payment status and placing date (store's "
            "local days, inclusive); `search` matches the order id, the "
            "customer's email or name. Requires `admin:orders:read`."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        params = request.query_params
        orders = Order.objects.all()
        status = params.get("status")
        if status:
            if status not in OrderStatus.values:
                raise ValidationError(
                    {"status": "Unknown status."}, code="invalid_status"
                )
            orders = orders.filter(status=status)
        payment_status = params.get("payment_status")
        if payment_status:
            if payment_status not in PaymentStatus.values:
                raise ValidationError(
                    {"paymentStatus": "Unknown payment status."},
                    code="invalid_payment_status",
                )
            orders = orders.filter(payment_status=payment_status)
        # The report query's own date validation, under the list's names
        # (snake case: CamelCaseMiddleWare rewrites query keys).
        dates = StaffReportQuerySerializer(
            data={
                key: params[name]
                for key, name in (
                    ("start", "placed_from"),
                    ("end", "placed_to"),
                )
                if params.get(name)
            }
        )
        dates.is_valid(raise_exception=True)
        start = dates.validated_data.get("start")
        end = dates.validated_data.get("end")
        if start or end:
            first, last = turnover.window(start or end, end or start)
            if start:
                orders = orders.filter(created_at__gte=first)
            if end:
                orders = orders.filter(created_at__lt=last)
        search = _search(request)
        if search:
            match = (
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
            if search.isdigit():
                match |= Q(id=int(search))
            orders = orders.filter(match)
        rows = orders.prefetch_related("items").order_by("-created_at")[
            : _limit(request)
        ]
        return Response(
            StaffOrderSerializer([_order_row(o) for o in rows], many=True).data
        )


class StaffAgentOrderDetailView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_ORDERS_READ

    @extend_schema(
        operation_id="getAgentStaffOrder",
        responses={
            200: StaffOrderDetailSerializer,
            404: ErrorResponseSerializer,
            **_UNAUTHORIZED,
        },
        summary="One order in full (store-admin agent surface)",
        description=(
            "Lines, totals, statuses, shipping and the customer's name, "
            "email, city and country; never the street or the phone. "
            "Requires `admin:orders:read`."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request, pk: int) -> Response:
        order = get_object_or_404(
            Order.objects.prefetch_related("items").select_related("country"),
            pk=pk,
        )
        lines = [
            {
                "product_id": item.product_id,
                "name": item.product_name,
                "sku": item.sku or "",
                "quantity": item.quantity,
                "unit_price": item.price.amount,
                "line_total": item.total_price.amount,
            }
            for item in order.items.all()
        ]
        payload = {
            **_order_row(order),
            "items_total": order.total_price_items.amount,
            "shipping": _amount(order.shipping_price),
            "payment_fee": _amount(order.payment_method_fee),
            "discount": _amount(order.discount_amount)
            + _amount(order.loyalty_discount)
            + _amount(order.gift_card_amount),
            "shipping_carrier": order.shipping_carrier or "",
            "tracking_number": order.tracking_number or "",
            "city": order.city,
            "country": order.country_id,
            "customer_notes": order.customer_notes or "",
            "lines": lines,
        }
        return Response(StaffOrderDetailSerializer(payload).data)


def _report_range(request: Request) -> tuple[Any, Any, Any]:
    query = StaffReportQuerySerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    today = timezone.localdate()
    end = query.validated_data.get("end") or today
    start = query.validated_data.get("start") or (
        end - timedelta(days=settings.STAFF_AGENT_REPORT_DEFAULT_DAYS - 1)
    )
    if start > end:
        raise ValidationError(
            {"start": "The start is after the end."}, code="invalid_range"
        )
    bucket = query.validated_data.get("bucket")
    return start, end, turnover.Bucket(bucket) if bucket else None


_REPORT_PARAMS = [
    OpenApiParameter(
        name="start",
        type=OpenApiTypes.DATE,
        description="First local day, inclusive. Defaults to the "
        "server's report window before `end`.",
    ),
    OpenApiParameter(
        name="end",
        type=OpenApiTypes.DATE,
        description="Last local day, inclusive. Defaults to today.",
    ),
    OpenApiParameter(
        name="bucket",
        enum=turnover.Bucket.values,
        type=OpenApiTypes.STR,
        description="One row per bucket; omitted, one row for the range.",
    ),
]


def _sales_row(row: turnover.Row) -> dict[str, Any]:
    return {
        "period": row.period,
        "orders": row.orders,
        "units": row.units,
        "sales_net": row.sales_net,
        "sales_gross": row.sales_gross,
        "returns_net": row.returns_net,
        "turnover": row.turnover,
        "vat": row.net_vat,
        "average_order_value": row.average_order_value,
        "awaiting_collection": row.awaiting_collection,
    }


class StaffAgentSalesView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_SALES_READ

    @extend_schema(
        operation_id="getAgentStaffSalesReport",
        parameters=_REPORT_PARAMS,
        responses={200: StaffSalesReportSerializer, **_UNAUTHORIZED},
        summary="Sales and turnover (store-admin agent surface)",
        description=(
            "The admin's turnover report (`order/turnover.py`): goods, "
            "shipping and fees net of VAT, counted when an order shipped, "
            "less returns. Requires `admin:sales:read` and a role with "
            "`order.view_turnover_report`."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        start, end, bucket = _report_range(request)
        rows = turnover.figures(start, end, bucket)
        payload = {
            "start": start,
            "end": end,
            "bucket": bucket,
            "currency": settings.DEFAULT_CURRENCY,
            "rows": [_sales_row(row) for row in rows],
            "total": _sales_row(turnover.total(rows)),
        }
        return Response(StaffSalesReportSerializer(payload).data)


def _profit_row(row: profit.Profit) -> dict[str, Any]:
    return {
        "period": row.period,
        "revenue": row.revenue,
        "cogs": row.cogs,
        "gross_profit": row.gross_profit,
        "gross_margin": row.gross_margin,
        "payment_fees": row.payment_fees,
        "delivery_costs": row.delivery_costs,
        "other_expenses": row.other_expenses,
        "operating_profit": row.operating_profit,
        "cost_coverage": row.cost_coverage,
        "fee_coverage": row.fee_coverage,
    }


class StaffAgentProfitView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_PROFIT_READ

    @extend_schema(
        operation_id="getAgentStaffProfitReport",
        parameters=_REPORT_PARAMS,
        responses={200: StaffProfitReportSerializer, **_UNAUTHORIZED},
        summary="Profit (store-admin agent surface, owner only)",
        description=(
            "The admin's profit report (`order/profit.py`) with its cost "
            "and fee coverage. Requires `admin:profit:read` and a role "
            "with `order.view_profit_report`, which only the OWNER holds."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        start, end, bucket = _report_range(request)
        rows = profit.figures(start, end, bucket)
        payload = {
            "start": start,
            "end": end,
            "bucket": bucket,
            "currency": settings.DEFAULT_CURRENCY,
            "rows": [_profit_row(row) for row in rows],
            "total": _profit_row(profit.total(rows)),
        }
        return Response(StaffProfitReportSerializer(payload).data)


class StaffAgentProductsView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_CATALOG_READ

    @extend_schema(
        operation_id="listAgentStaffProducts",
        parameters=[
            _SEARCH_PARAM,
            OpenApiParameter(
                name="lowStock",
                type=OpenApiTypes.BOOL,
                description="Only products at or under their low-stock "
                "threshold, lowest stock first.",
            ),
            _LIMIT_PARAM,
        ],
        responses={200: StaffProductSerializer(many=True), **_UNAUTHORIZED},
        summary="Products, prices and stock (store-admin agent surface)",
        description=(
            "`search` matches the name, SKU or id. Requires "
            "`admin:catalog:read`."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        products = Product.objects.all()
        search = _search(request)
        if search:
            match = Q(translations__name__icontains=search) | Q(
                sku__icontains=search
            )
            if search.isdigit():
                match |= Q(id=int(search))
            products = products.filter(match).distinct()
        if request.query_params.get("low_stock") in {"true", "1"}:
            products = products.filter(
                stock__lte=F("low_stock_threshold")
            ).order_by("stock", "id")
        else:
            products = products.order_by("-updated_at", "-id")
        rows = []
        for product in products.prefetch_related("translations")[
            : _limit(request)
        ]:
            price = product.final_price
            rows.append(
                {
                    "id": product.id,
                    "name": product.safe_translation_getter(
                        "name", any_language=True
                    )
                    or "",
                    "sku": product.sku or "",
                    "price": product.price.amount,
                    "final_price": price.amount,
                    "currency": str(price.currency),
                    "stock": product.stock,
                    "low_stock_threshold": product.low_stock_threshold,
                    "low_stock": product.stock <= product.low_stock_threshold,
                    "published": product.active,
                }
            )
        return Response(StaffProductSerializer(rows, many=True).data)


class StaffAgentCustomersView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_CUSTOMERS_READ

    @extend_schema(
        operation_id="listAgentStaffCustomers",
        parameters=[_SEARCH_PARAM, _LIMIT_PARAM],
        responses={200: StaffCustomerSerializer(many=True), **_UNAUTHORIZED},
        summary="The store's customers (store-admin agent surface)",
        description=(
            "Name, email, when they joined and their orders; never an "
            "address or a phone. `search` matches the email or name. "
            "Requires `admin:customers:read`."
        ),
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        customers = UserAccount.objects.annotate(
            order_count=Count("orders"), last_order_at=Max("orders__created_at")
        )
        search = _search(request)
        if search:
            customers = customers.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
        rows = [
            {
                "id": customer.id,
                "name": f"{customer.first_name} {customer.last_name}".strip(),
                "email": customer.email,
                "joined_at": customer.created_at,
                "is_active": customer.is_active,
                "orders": customer.order_count,
                "last_order_at": customer.last_order_at,
            }
            for customer in customers.order_by("-created_at")[: _limit(request)]
        ]
        return Response(StaffCustomerSerializer(rows, many=True).data)


class StaffAgentReviewsView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_REVIEWS_READ

    @extend_schema(
        operation_id="listAgentStaffReviews",
        parameters=[
            OpenApiParameter(
                name="status",
                enum=["NEW", "TRUE", "FALSE"],
                type=OpenApiTypes.STR,
                description="NEW waits for approval, TRUE is approved, "
                "FALSE rejected.",
            ),
            _LIMIT_PARAM,
        ],
        responses={200: StaffReviewSerializer(many=True), **_UNAUTHORIZED},
        summary="Product reviews, newest first (store-admin agent surface)",
        description="Requires `admin:reviews:read`.",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        from product.enum.review import ReviewStatus

        reviews = ProductReview.objects.select_related(
            "product"
        ).prefetch_related("translations", "product__translations")
        status = request.query_params.get("status")
        if status:
            if status not in ReviewStatus.values:
                raise ValidationError(
                    {"status": "Unknown status."}, code="invalid_status"
                )
            reviews = reviews.filter(status=status)
        rows = [
            {
                "id": review.id,
                "product_id": review.product_id,
                "product_name": review.product.safe_translation_getter(
                    "name", any_language=True
                )
                or "",
                "rating": review.rate,
                "status": review.status,
                "status_display": review.get_status_display(),
                "published": review.is_published,
                "created_at": review.created_at,
                "comment": review.safe_translation_getter(
                    "comment", any_language=True
                )
                or "",
            }
            for review in reviews.order_by("-created_at")[: _limit(request)]
        ]
        return Response(StaffReviewSerializer(rows, many=True).data)


class StaffAgentMessagesView(StaffAgentAPIView):
    staff_scope = SCOPE_STAFF_MESSAGES_READ

    @extend_schema(
        operation_id="listAgentStaffMessages",
        parameters=[_LIMIT_PARAM],
        responses={200: StaffMessageSerializer(many=True), **_UNAUTHORIZED},
        summary="Contact messages, newest first (store-admin agent surface)",
        description="Requires `admin:messages:read`.",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        rows = [
            {
                "id": message.id,
                "name": message.name,
                "email": message.email,
                "subject": message.subject or "",
                "created_at": message.created_at,
                "message": message.message,
            }
            for message in Contact.objects.order_by("-created_at")[
                : _limit(request)
            ]
        ]
        return Response(StaffMessageSerializer(rows, many=True).data)
