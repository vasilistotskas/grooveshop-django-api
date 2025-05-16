from django.urls import path

from order.views.item import OrderItemViewSet
from order.views.order import OrderViewSet

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
        "order/<int:pk>/add-tracking",
        OrderViewSet.as_view({"post": "add_tracking"}),
        name="order-add-tracking",
    ),
    path(
        "order/<int:pk>/update-status",
        OrderViewSet.as_view({"post": "update_status"}),
        name="order-update-status",
    ),
    path(
        "order/my-orders",
        OrderViewSet.as_view({"get": "my_orders"}),
        name="order-my-orders",
    ),
    path(
        "order-items",
        OrderItemViewSet.as_view({"get": "list"}),
        name="order-item-list",
    ),
    path(
        "order-items/<int:pk>",
        OrderItemViewSet.as_view({"get": "retrieve"}),
        name="order-item-detail",
    ),
    path(
        "order-items/<int:pk>/refund",
        OrderItemViewSet.as_view({"post": "refund"}),
        name="order-item-refund",
    ),
]
