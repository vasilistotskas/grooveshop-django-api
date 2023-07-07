from order.views import OrderViewSet
from django.urls import path

urlpatterns = [
    path(
        "order/",
        OrderViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "order/<int:pk>/",
        OrderViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
]
