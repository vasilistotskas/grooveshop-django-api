from django.urls import path

from order.views.item import OrderItemViewSet
from order.views.order import OrderViewSet
from order.views.payment import OrderPaymentViewSet

urlpatterns = [
    path(
        "order",
        OrderViewSet.as_view({"get": "list", "post": "create"}),
        name="order-list",
    ),
    path(
        "order/<int:pk>",
        OrderViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="order-detail",
    ),
    path(
        "order/uuid/<uuid:uuid>",
        OrderViewSet.as_view({"get": "retrieve_by_uuid"}),
        name="order-retrieve-by-uuid",
    ),
    path(
        "order/<int:pk>/cancel",
        OrderViewSet.as_view({"post": "cancel"}),
        name="order-cancel",
    ),
    path(
        "order/<int:pk>/add_tracking",
        OrderViewSet.as_view({"post": "add_tracking"}),
        name="order-add-tracking",
    ),
    path(
        "order/<int:pk>/update_status",
        OrderViewSet.as_view({"post": "update_status"}),
        name="order-update-status",
    ),
    path(
        "order/my_orders",
        OrderViewSet.as_view({"get": "my_orders"}),
        name="order-my-orders",
    ),
    path(
        "order/<int:pk>/process_payment",
        OrderPaymentViewSet.as_view({"post": "process_payment"}),
        name="order-process-payment",
    ),
    path(
        "order/<int:pk>/payment_status",
        OrderPaymentViewSet.as_view({"get": "check_payment_status"}),
        name="order-payment-status",
    ),
    path(
        "order/<int:pk>/refund",
        OrderPaymentViewSet.as_view({"post": "refund_payment"}),
        name="order-refund",
    ),
    path(
        "order-items",
        OrderItemViewSet.as_view({"get": "list", "post": "create"}),
        name="order-item-list",
    ),
    path(
        "order-items/<int:pk>",
        OrderItemViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="order-item-detail",
    ),
    path(
        "order-items/<int:pk>/refund",
        OrderItemViewSet.as_view({"post": "refund"}),
        name="order-item-refund",
    ),
]
