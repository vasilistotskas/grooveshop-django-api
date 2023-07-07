from django.urls import path

from order.views import OrderViewSet

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
